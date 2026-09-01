"""Vikunja REST client. Gotchas baked in: PUT=create, POST=full-replace update -> RMW.

DOSSIER: `docs/dossier/api.md` — the measured evidence under the rules in this
module: why a guessed page size is never acceptable and how it
reaped a live worktree.
Read it before changing a guard here; CLAUDE.md carries only the rule.
"""
import time
from typing import Any

import httpx

from .formatting import text_to_html


class VikunjaError(Exception):
    def __init__(self, status: int, message: str):
        self.status = status
        self.message = message
        super().__init__(f"Vikunja API {status}: {message}")


# VMCP-92: the request ceiling of a board read the server's own stated page size does not justify
# — see the long note above `_page_size`. DERIVED, not picked: `workspace_cmd._READ_DEADLINE_
# SECONDS` (30 s) is the budget a human already decided a WHOLE tracker read may take, and the
# VMCP-72 comment MEASURED the healthy read against the real tracker at four requests in
# 0.89-1.10 s, i.e. ~0.25 s/request. 30 / 0.25 = 120, so the callers with NO deadline
# (next_task/claim/advance/setup) get, in requests, the containment `--gc` already has in seconds —
# and machine-independently, since it comes from a configured budget and a measured rate rather
# than from a loopback page count.
#
# VMCP-103 renamed it from `_UNKNOWN_PAGE_SIZE_MAX_PAGES`: it is no longer the DEGRADED branch's
# ceiling, it is every read's, counted over the pages `max_items_per_page` did NOT account for. An
# unknown page size justifies nothing, so a degraded read spends the budget page by page exactly
# as before; a healthy read spends it only on pages where no required bucket came back full, which
# is why an honest 8 000-task board still reads whole in 161 requests.
_MAX_UNPROVEN_PAGES = 120


# VMCP-127 (608) — THE FULLNESS INFERENCE IS GONE FROM BOTH READERS, AND WITH IT THE LAST PLACE
# THIS CLIENT CONCLUDED "DONE" FROM A PAGE'S LENGTH.
#
# What used to be here was `_could_be_full(stated, longest_served) = min(stated, longest_served)`,
# the ONE expression VMCP-89/92/103 each got wrong in a branch the previous card had not touched,
# and VMCP-111 (582) then pinned both operands of. Both readers asked it the same question — "could
# this page still be full?" — and treated NO as "that bucket/list is exhausted". The bar was never
# the defect; the INFERENCE was. A page short of the server's real page size can perfectly well
# have rows behind it (VMCP-103's short non-final page), so the bar only ever decided WHICH servers
# a read got away with.
#
# MEASURED on this tree before anything was changed (real httpx over MockTransport, real api.py,
# 2026-07-31 — a DATED SNAPSHOT, not a permanent fact). /info states max_items_per_page=5; page 1
# serves EIGHT; page 2 REPEATS a window of w already-seen rows; page 3 holds three more; a second
# bucket / one new row per page keeps "nothing new arrived" from ending the read on its own:
#
#     w      nested healthy  nested degraded  flat healthy  flat degraded
#     1..4   LOSS            LOSS             LOSS          LOSS
#     5..7   whole           LOSS             whole         LOSS
#     8      whole           whole            whole         whole
#
# — i.e. the healthy read lost rows for every w < the STATED size and the degraded one for every
# w < the longest SERVED, which is the whole of VMCP-124 (603)'s finding: the healthy/degraded gap
# was a symptom of a rule both branches shared. And the CONTROL that names the real trigger, run
# because 603's review asked for it: page 1 serving EXACTLY the stated 5, no over-serving anywhere,
# loses the same rows on the HEALTHY read for every w < 5. The trigger is a short non-final REPEAT
# window; an over-serving server only widens the DEGRADED band.
#
# THE RULE NOW, in both readers: keep reading while the page brought something NEW — and for the
# BOARD reader that is wider than "new in a required bucket", which is what this sentence used to
# say before VMCP-144 (633) measured it. The board's rule is
# `added_new_required or (required_had_tasks and added_new)`, and the second disjunct fires on a
# page where a required bucket merely came back with tasks it had already served while some OTHER
# bucket added something. Constructed and measured through the real `view_tasks` over a
# MockTransport: page 2 repeated the required bucket's only task and added a NEW task to Done, and
# the loop asked for page 3. So "nothing else" was false, and — worse — the clause it described is
# the FIRST disjunct, the one that cannot decide anything (see the note at `keep_going` for why).
# The flat reader's rule really is just "something new".
# Either way it is a strict SUPERSET of what shipped before on EVERY
# server — not by an argument about page sizes this time (that is the argument 603 measured false)
# but by monotonicity: the old rules were `added_new AND (maybe_full OR header_more)` and
# `added_new_required OR (maybe_full_required AND added_new)`, and dropping a conjunct / weakening
# a guard can only make a keep-going fire MORE often. It also takes /info out of the STOP RULE
# itself, so healthy and degraded now execute the same stop rule by construction rather
# than by a claim — which is what VMCP-103 was for and what 603 found broken. Read that as a
# statement about the RULE and never as "/info cannot change where a read stops", which is the
# stronger sentence this comment used to carry. `_MAX_UNPROVEN_PAGES` counts pages the STATED size
# did not account for, so the two branches can diverge at the ceiling — the measured pair is in
# the note above `_page_size`, under «ЗДЕСЬ ОН БЮДЖЕТ», where a degraded read 508s and a healthy
# one reads on. "CAN", not "does", and the condition is the page LENGTH rather than the ceiling:
# divergence needs at least one page the stated size ACCOUNTS FOR, so on a shape where no page
# ever reaches it the two are identical at and ABOVE the ceiling too — measured, 120 one-row pages
# raise a 508 on request 120 with /info up and with /info down alike.
#
# WHAT IT COSTS, measured rather than assumed. Three entries, and they are the costs AS MEASURED
# rather than a point-by-point reply to the card's four bullets — so do not read the colon as a
# promise of four. Of those four, TWO were wrong as stated: "+1 request on EVERY flat `_paged_list`
# read" (the first entry below carries the rule that actually holds) and "both of VMCP-111 (582)'s
# constructed tests would need rewriting" (measured: ONE assertion in ONE test — the nested pin's
# docstring was rewritten, not one of its assertions). The other two held. Two costs have no entry
# here: the card's test rewrite, because it is not a runtime cost, and the randomized sweeps'
# healthy-vs-degraded cross-check going tautological, recorded in test_api_kanban.py where those
# sweeps live. The third entry below carries one the card never named at all — the FLAT twin of
# the RAISE:
#   * flat reads: +1 request only when the read spans >=2 pages AND its last content page is short
#     of the old bar. +0 on a single partial page (the bar was 0 on page 1, so that request was
#     always paid), on full-page lists, on `?page=`-ignoring endpoints, on an empty list. LIVE on
#     2.3.0 at max_items_per_page=5: labels() 20 rows in four full pages +0; labels() 22 rows +1 on
#     BOTH branches; projects() 35 real +0; projects() 37 real +0 healthy / +1 degraded;
#     views()/buckets()/comments()/view_tasks() +0. Workflow.next_task() end to end: 7 requests
#     before and 7 after.
#   * the board read: +0 on every honest and every live shape.
#   * a NEW 508. A required bucket that re-serves a NON-EMPTY window forever while some other
#     bucket keeps producing used to stop at 2 requests with a complete board and now spends the
#     `_MAX_UNPROVEN_PAGES` budget and RAISES (measured: 2 requests -> 121 and a 508); the flat
#     twin is a list that never stops producing (shipped returned an arbitrary 6 rows of it). Both
#     are constructed: 2.3.0 serves an EMPTY window past a bucket's end, measured live (windows
#     5,5,2,0). And the client cannot tell that server from the one where the same repeat hides
#     three more tasks — where shipped truncated SILENTLY and this rule reads whole. Trading a
#     quiet wrong answer for a loud absent one is this module's standing invariant, not a new risk.
#
# NOT FIXED, and deliberately: "a page that brought nothing new ends the read" is ALSO an
# inference, and it is also unsound (a window filtered down to nothing, then more behind it). It
# stays because the alternative was measured worse — VMCP-116 (589) clocked a header-carried read
# at 41+ requests where this one spends 2 on the `?page=`-ignoring endpoints 2.3.0 really serves.
# So this card removes ONE of the two unsound stops, not both, and the read is still not complete
# by construction.
#
# WHAT WENT WITH IT: `_total_pages` and `_req(with_headers=)`. The header was the flat reader's
# complement to the fullness bar — the one signal that could see rows behind a SHORT non-final page
# — and it reached the stop rule only ORed with `maybe_full`. With `maybe_full` gone it could never
# change an outcome again, so it is deleted rather than left as inert code. What it was worth is
# worth keeping written down: `x-pagination-total-pages` is wrong in BOTH directions on one and the
# same 2.3.0 (VMCP-103 measured it UNDER-reporting on the kanban tasks endpoint, result-count 3 /
# total-pages 1 over a bucket holding 3 pages; VMCP-108 measured it OVER-reporting on
# /projects/{id}/views and .../buckets), which is why it was only ever believed when it said "keep
# going". If any shortness inference ever comes back, this comes back with it.


def _fold_host(host: str) -> str:
    """Lowercase the case-INSENSITIVE part of a host slice, and only that (tracker #707).

    This exists because `host` is not homogeneous, which is the one thing canonical_base_url's
    shape (`scheme.lower()` + userinfo verbatim + host + path verbatim) could not say. RFC 3986
    6.2.2.1 makes the host case-insensitive; RFC 6874 then grafts an IPv6 ZONE ID into that same
    production (`IPv6addrz = IPv6address "%25" ZoneID`) WITHOUT saying it inherits that property.
    Read off the document rather than measured: RFC 6874 says nothing about the zone's case at all
    (its only nearby line is "A <zone_id> SHOULD contain only ASCII characters classified as
    unreserved"). The semantics therefore come from MEASUREMENT, and the two must not be blurred:
    a zone id is an operating-system INTERFACE NAME, and interface names are case-SENSITIVE. On
    2026-08-03 `socket.if_nametoindex` rejected the upper-cased spelling of every interface that
    has a lower-case letter — 11 of 11 on Linux (python:3.12-alpine, the platform CI runs, `ETH0`
    -> OSError("no interface with this name")) and 26 of 26 on this darwin box. Stronger, because
    it is the path a URL actually takes: `socket.getaddrinfo` returns a DIFFERENT sockaddr —
    `::1%lo0` -> `('::1', 443, 0, 1)` against `::1%LO0` -> `('::1', 443, 0, 0)`, i.e. the zone is
    silently DROPPED rather than matched case-insensitively. (The interface INDEX itself is not a
    stable figure — a container's veth index moves between runs — so the signal is the OSError and
    the differing scope, never the number.) Python's stdlib models it the same way:
    `IPv6Address('fe80::1%ETH0').scope_id` keeps `ETH0` and compares UNEQUAL to `...%eth0`
    (different hashes), while `IPv6Address('FE80::1') == IPv6Address('fe80::1')` is True and
    str()s to `fe80::1`.

    So the hex folds and the zone does not. Folding the zone collapsed two DIFFERENT interfaces
    onto one string — the permissive direction #148 exists to close and #164 removed from
    userinfo, for a shape nobody had named. Not reachable from a real config (a Vikunja base url
    is `https://tracker.zz.hgdev.com`), so this fixes an unclosed part of an accepted decision
    rather than a live harm; on the zone specifically it also brings this function into agreement
    with httpx, measured to pass `[fe80::1%25ETH0]` through verbatim. Only on the zone: httpx does
    not fold the ADDRESS's hex either (`[FE80::1]` comes back unchanged) and this function still
    does, so the two agree here and still diverge one component over.

    The two cuts are ABNF boundaries, not guesses. The literal ends at `]` (`IP-literal = "[" (
    IPv6address / IPv6addrz / IPvFuture ) "]"`), so a port — and, until #706, a query or fragment
    that landed in this slice — keeps folding exactly as before. Inside the brackets the zone
    starts at the FIRST `%`, and it is the FIRST one specifically that cannot be anything else:
    `IPv6address` has no `%`, and `IPvFuture = "v" 1*HEXDIG "." 1*( unreserved / sub-delims / ":" )`
    admits none either (`%` is in neither set; httpx refuses IPvFuture outright besides —
    `httpx.URL('https://[v1.aB:c]/api/v1')` raises InvalidURL). A LATER `%` inside the brackets very
    much can be something else: `ZoneID = 1*( unreserved / pct-encoded )` and `pct-encoded = "%"
    HEXDIG HEXDIG`, so `[fe80::1%25ETH%2D0]` is a legal zone containing a second `%`. That is why
    the cut is `partition`, not `rpartition` — pinned, `rpartition` reddens 5 tests.

    Cutting at the first `%` also protects the sloppy bare-`%` spelling `[fe80::1%ETH0]`, which
    httpx accepts and preserves. All of this makes the function fold LESS — verified by exhaustion,
    not by argument: over a 430,332-url grid and a 2.3M-url case fuzz, ZERO pairs the old body
    distinguished are collapsed by this one. Folding less is the RIGHT trade here but it is not a
    free one, and calling it "the only safe direction" would be false: less folding buys fewer
    silent repoints at the cost of possible false REFUSALS, and one is measured. RFC 3986 2.1 makes
    percent-encoding hex digits case-INSENSITIVE ("If two URIs differ only in the case of
    hexadecimal digits used in percent-encoded octets, they are equivalent"), so `[fe80::1%25ETH%2D0]`
    and `[fe80::1%25ETH%2d0]` are one endpoint — and the guard, which ACCEPTED that rotation before
    #707, now REFUSES it. It is unreachable rather than harmless: httpx rejects that url outright
    (`InvalidURL: Invalid IPv6 address`), so the client cannot be built on either spelling and the
    failure is fail-closed. Normalizing pct-encoding case is deliberately NOT done, and the honest
    scope of that is NARROWER than "nowhere" — #707's own reviewer disproved the wider wording by
    construction and it is corrected here rather than left standing. Measured on this tree: the
    path, the query and the zone all keep `%2F` and `%2f` APART, but a reg-name HOST folds them
    together (`https://h%2Dt.example` and `https://h%2dt.example` canonicalize to ONE string),
    because `.lower()` on a reg-name reaches an octet's hex digits like any other character. So the
    zone is an EXCEPTION to what this function does on the component next door, not an instance of a
    uniform policy. Which way each one goes is nevertheless right: on a reg-name the fold is what
    RFC 3986 2.1 licenses, and on the zone the refusal is the deliberate false one measured above.
    Real pct normalization for one component would be a wider change than this card.

    NOT this function's business, deliberately: the query/fragment that reaches it inside `host`
    when it appears before the first `/`. That is a slicing bug one line up (`rest.partition("/")`
    was blind to `?` and `#`) rather than an exception inside the host, and it was filed as #706 —
    which has since LANDED, so `https://t.example?Q=A` now keeps its `?Q=A`. Measured before that
    landing that the two fixes sit on DIFFERENT lines and compose, and measured after it on the url
    that carries both: `https://[fe80::1%25ETH0]?Q=A` keeps the zone AND the query. That url is the
    seam between the two cards, and it is pinned by one assert in tests/unit/test_api.py."""
    if host.startswith("["):
        literal, bracket, tail = host.partition("]")
        address, percent, zone = literal.partition("%")
        return f"{address.lower()}{percent}{zone}{bracket}{tail.lower()}"
    return host.lower()


def canonical_base_url(base_url: str) -> str:
    """Canonicalize a Vikunja base URL — the SINGLE normalization shared by the client (which builds
    requests from it) and the 401 repoint guard in server.py (which compares a reloaded config's url
    against the running session's). Kept as one function so the two can NEVER drift apart (tracker
    #154: they had — the guard compared the RAW url while the client normalized it, so a cosmetic-only
    difference read as a mid-session host change and refused a healthy token rotation, inverting #148).

    Folds ONLY what is the same endpoint by definition, and keeps every genuine change:
      * strips a trailing slash — cosmetic;
      * lowercases the scheme and the HOST[:port] — the RFC-3986 case-insensitive parts — and only
        as far as the AUTHORITY actually reaches. RFC 3986 3.2 ends the authority at the first `/`,
        `?` or `#`, or at the end of the URI; the slice below implemented one of those three
        terminators until #706, so a query or fragment written before any `/` fell INSIDE the
        authority and was lowercased along with the host (`https://h?Q=A` -> `https://h?q=a`,
        `https://h#Frag` -> `https://h#frag`). Measured through the real repoint guard, that made
        `server._reload_workflow_from_disk` ACCEPT a rotation differing only in the case of a
        query — the same permissive over-fold #164 removed from userinfo, in a shape #164's own
        grid could not contain. Using all three terminators is what fixes it, and it is one rule
        rather than a case each for `?` and `#`. httpx folds scheme and host the same way when it
        builds a request, so routing the client through this leaves the request it builds unchanged
        for every url a Vikunja base url can be (the existing api tests pass untouched). NOT for
        every url, though — #154 said "identical" flat, and #164 measured THREE divergences on
        httpx 0.28.1, none reachable from a real config; #706 closed the third, and two of THAT
        LIST remain: an uppercase scheme with an explicit DEFAULT port (`HTTPS://h:443` keeps
        `:443` on the wire, the canonicalized `https://h:443` drops it, because that drop is
        case-sensitive about the scheme), and an IPv6 literal written with UPPERCASE HEX
        (`[::FFFF:1]` folds to `[::ffff:1]`, changing the Host header). Both are the same endpoint
        either way, and both predate #164 — they come from folding the scheme and host, which is
        #154's. The point is that "identical" was wider than its measurement; the second test in
        tests/unit/test_api.py pins the rule AND both, and pins the closed shape from the other
        side. A FOURTH went unnamed until #707: the host was folded whole, so an IPv6 ZONE ID went
        down with the hex (`[fe80::1%25ETH0]` -> `[fe80::1%25eth0]`). That one is not the same
        endpoint — a zone id is an OS interface name and interface names are case-sensitive
        (measured, both platforms) — so it was the userinfo defect again in a shape the words "IPv6
        literal" hid. It OVERLAPS class 2 without either containing the other, and saying it "hid
        inside" class 2 would be false: measured, `[fe80::1%25ETH0]` carries no uppercase hex digit
        in its ADDRESS half yet diverged before #707, so class 2 as written never covered it. (The
        qualifier is #707's reviewer's, and it is load-bearing rather than pedantic: the string does
        hold an uppercase `E`, in `ETH0` — reading it as "no uppercase hex digit at all" is exactly
        the "a literal is homogeneous" conflation this card exists to remove, made about the
        card's own evidence.) `[FE80::1%25ETH0]` is in both; `[::FFFF:1]` is in class 2 only and
        still diverges.
        Class 2 is therefore unchanged by #707, not shrunk. Read #706 and #707 together and the
        lesson is one: "#164 measured three" counted a LIST, and a list is not the world — one of
        the three was fixable and a fourth was never on it. The host's case rule now lives in
        `_fold_host` above, which is where the hex-vs-zone split is argued and measured;
      * ensures the `/api/v1` suffix.
    It deliberately does NOT touch the scheme VALUE (http vs https — a plaintext downgrade is REAL),
    the host, the port, or the path (all case-sensitive): a rotation moving any of those is a genuine
    repoint the guard must still refuse.

    USERINFO IS NOT FOLDED, and that is tracker #164's fix rather than an omission. The authority
    used to be lowercased WHOLE, so `https://u:PassWord@h` came back as `https://u:password@h` — a
    different CREDENTIAL collapsed onto one string, i.e. the guard reading two of them as the same
    endpoint, in the permissive direction #148 exists to close. RFC 3986 6.2.2.1 normalizes the case
    of the scheme and the host and of nothing else, and assumes every other generic-syntax component
    case-sensitive unless a scheme defines otherwise — http and https do not, so userinfo and path
    are case-sensitive. (That section names neither component individually, and 3.2.1 deprecates the
    `user:password` form outright, so do not restate this as an RFC rule about "passwords".) httpx
    agrees: measured, `httpx.URL('https://User:PassWord@HOST/api/v1')` reads host `host` and
    userinfo `b'User:PassWord'`. Folding it was the only class where this function changed a
    CREDENTIAL the client then sent — NOT the only class where it changed the request at all; the
    classes named above are the ones MEASURED, which is not the same as all there are. Swept over 540
    constructed urls (3 schemes x 6 userinfo shapes x 5 hosts x 6 paths), comparing each body
    against the pre-#154 raw path through `httpx.URL`: the old one differed on 288, this one on 36,
    and all 36 of those are the uppercase-scheme + default-port class — userinfo has stopped being a
    difference at all. That grid is a GRID, though, and the IPv6-hex and query-before-slash classes
    are precisely shapes it cannot contain; they were found by looking outside it, which is the
    standing reason not to read 36 as a total. TWO classes were found outside it, and they were
    found in two different ways — worth stating exactly rather than atmospherically. The query
    class (#706) came out of #164's own second independent pass, i.e. from reading the text against
    its evidence, not from any sweep at all. The zone-id class (#707) surfaced in the HOST bucket of
    #164's REVIEWER's own sweep — 16,320 urls, 5 schemes x 8 userinfo shapes x 24 hosts x 17 paths,
    per that reviewer's second pass, which corrected the factors it had first written while
    confirming the total. What made that one visible is not that the grid was "outside" this one
    but that it varied 24 hosts where this one varies 5. Both are grids; neither total could have
    named the class it does not contain. The split is on the LAST `@`, which is httpx's too
    Read "CREDENTIAL" above operationally: the one the request AUTHENTICATES with, i.e. the
    `Authorization` header. httpx derives BasicAuth from a url's userinfo, and because this
    constructor passes no `auth=`, that derivation REPLACES the `Authorization: Bearer` it sets —
    measured on httpx 0.28.1 through a real client, base `https://User:PassWord@t.example` sent
    `Basic` over `user:password` under the pre-#164 body and over `User:PassWord` under this one,
    one Authorization header on the wire either way. (A bare `@` with neither user nor password
    derives nothing and the Bearer stands; an explicit `auth=` would beat the url.) Folding a
    query-before-slash never moved that header: with no userinfo present it stayed `Bearer …`
    byte-identical across the pre-#164, pre-#706 and current bodies. That was never why the query
    class mattered — it was the same permissive over-fold, and #706 closed it because the guard
    read two urls as one endpoint, not because a header moved; a url carrying BOTH is moved by its
    userinfo, not by its query (`https://User:PassWord@t.example?apiToken=SeCrEt` diverges, and its
    query folded under BOTH earlier bodies and is kept verbatim only by this one). Pinning the word
    down is not decoration: #164 lost a review round to a reader who took CREDENTIAL to include a
    secret carried in the query string.

    WHY NOT `urllib.parse.urlsplit`. #706 weighed replacing the hand slicing with the stdlib parser
    — "fold exactly the case-insensitive components and let a parser find them" — and MEASURED it
    against this body over one grid instead of arguing it. The parser route loses on four counts,
    THREE of them in the PERMISSIVE direction this function exists to avoid and the fourth a new
    crash class: `urlunsplit` DROPS an empty `?` or `#` (`https://h?` -> `https://h`, which RFC
    3986 6.2.3 declines to license and names as its own example), `urlsplit` silently DELETES
    tab/CR/LF from anywhere in the url (a host written `h<TAB>x.example` reads back as netloc
    `hx.example` — a different HOST, in silence), and `urlsplit` reads a scheme where there is none
    (`Example.COM:3456` -> scheme `example.com`, folding a url this body leaves verbatim for want
    of a `://`). The fourth: `urlsplit` RAISES `ValueError` on an unclosed IPv6 literal in an
    AUTHORITY (`https://[fe80::1`; the same text in a path or query parses fine) where this body
    raises nothing ever and is called from `VikunjaAPI.__init__` and the stdio server's guard.
    Keep the slicing.

    The PATH's case is likewise kept, and #164 pins it: `https://h/vikunja` and `https://h/Vikunja`
    are different endpoints on a case-sensitive server. That was already true when #154 wrote it —
    what was missing is any test that would notice it stopping being true. #154's own reviewer ran
    the "lowercase the path too" mutation and the whole suite stayed green; #164 re-ran it before
    changing anything and got the same result, then again after, where it reddens the four tests
    named in tests/unit/test_api.py's MUTATION-CHECKED record."""
    prefix, sep, rest = base_url.partition("://")
    if sep:
        # RFC 3986 3.2: the authority ends at the FIRST "/", "?" or "#", or at the end of the URI.
        # All three terminators, not just the slash — #706; `tail` is kept verbatim, like the path.
        cut = min((i for i in map(rest.find, "/?#") if i >= 0), default=len(rest))
        authority, tail = rest[:cut], rest[cut:]
        userinfo, at, host = authority.rpartition("@")     # ("", "", authority) when there is no @
        base = f"{prefix.lower()}{sep}{userinfo}{at}{_fold_host(host)}{tail}"
    else:
        base = base_url
    base = base.rstrip("/")
    if not base.endswith("/api/v1"):
        base += "/api/v1"
    return base


def label_key(title: str) -> str:
    """The ONE statement of "what the server thinks this label title is" — `.strip().casefold()`.

    Vikunja labels are matched by TITLE and the server is generous about spelling: this package
    resolves `Bug`, `bug ` and `BUG` to the one label `bug`, on purpose, because a bot typing a
    variant once forked a colorless duplicate beside the canonical label (real incident
    2026-07-08, recorded in `get_or_create_label` below). That rule used to live ONLY in
    `get_or_create_label`, i.e. only on the WRITE path, while every gate in `workflow` asked
    `lb["title"] == title` — EXACT. The two therefore disagreed about what "this label" means,
    and #1216 closed exactly one instance of that disagreement (the guard inside
    `Workflow._add_label`, re-keyed to the resolved label ID — #1456 has since returned THAT
    guard to `_has_label`, on a probe of this server; the ID keying is history, the class below is
    not). #1256 closed the class: BOTH
    title comparisons in `workflow` — `_has_label`, read at thirteen CALL SITES (twelve source
    lines; two of those thirteen compute `review_kind` rather than gate anything), and
    `_remove_label` — now come through here, and `tests/unit/fakes.py` borrows it rather than
    restating it, which it did until #1256's own second pass found the copy.

    IT IS A COMPARISON KEY, NOT A TITLE. It never decides what gets WRITTEN to the board — a
    label is created with the title its caller typed, and the board keeps whatever spelling a
    human chose. What this normalises is only the question "is THAT label THIS one?".

    Why `casefold()` and not `lower()`: `lower()` is a per-character map and misses the pairs
    Unicode folds specially — checked, `"STRASSE".lower() == "straße".lower()` is False while
    their casefolds are equal. Neither is a claim about what the SERVER folds, and the evidence
    here does not reach that far: what the 2026-07-08 incident and
    `tests/integration/test_duplicate_label.py` show is that the server does not fold ON CREATE —
    a variant becomes its own row — and nothing here measures a server-side title comparison at
    all, since attaching a label is by `label_id`. This is the CLIENT deciding which of those
    rows it will treat as one, and it should err towards treating MORE of them as one: a false
    split re-forks the duplicate the rule exists to prevent, while a false merge would need two
    labels a human means differently and spells the same modulo case and surrounding space,
    which none of this package's six are."""
    return (title or "").strip().casefold()


class VikunjaAPI:
    def __init__(
        self, base_url: str, token: str, client: httpx.Client | None = None,
        *, timeout: float = 30, max_retries: int | None = None,
        event_hooks: dict | None = None,
    ):
        """`timeout`/`max_retries` exist for ONE caller: `workspace --gc`, whose board read
        happens while it holds the repo-wide worktree flock (see workspace_cmd._build_workflow).
        `event_hooks` now has TWO — gc hangs its read deadline there, and `claimable` hangs its
        stderr breadcrumb trail (claimable_cmd._Trail, VMCP-85), which is why the parameter is
        described below as httpx's own hook mapping rather than as gc's deadline slot.
        Everything else keeps the defaults. All three are ignored
        when `client` is supplied — the caller then owns the whole client (tests pass a
        MockTransport one, and a test that wants a hook builds it into its own client).

        `event_hooks` is httpx's own {"request": [...], "response": [...]} mapping. It is here
        rather than assembled by the caller so that gc's client is still built by THIS
        constructor: duplicating the base-url canonicalisation and the Authorization header at a
        second call site is how one of them silently stops matching the other."""
        self._client = client or httpx.Client(
            base_url=canonical_base_url(base_url),
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout,
            event_hooks=event_hooks,
        )
        if max_retries is not None:
            # an INSTANCE attribute shadowing the class default below, for this client only
            self._MAX_RETRIES = max_retries
        # resolved ONCE per client, and the flag is separate from the value because "unknown" is
        # now a real answer (None) rather than a guess — see _page_size / VMCP-89.
        self._page_size_cache: int | None = None
        self._page_size_resolved = False

    # --- транзиентные ретраи (#86 «восстановление работы на ошибках апи») ---
    # Раньше _req падал с ПЕРВОЙ же 429/5xx/обрыва связи, и работа агента вставала на
    # ровном месте. Ретраим с backoff, но безопасно к семантике PUT=create/POST=replace:
    #   - 429: сервер ОТКЛОНИЛ запрос ДО применения -> ретраим ЛЮБОЙ метод (чтим Retry-After);
    #   - 5xx и обрыв/таймаут связи: исход неоднозначен (могло примениться) -> ретраим только
    #     идемпотентные GET и POST (POST = полная перезапись, повтор даёт то же состояние).
    #     PUT (create) и DELETE на этих ошибках НЕ ретраим — иначе дубль или ложная 404.
    # Постоянные ошибки (4xx кроме 429) поднимаются сразу, как и прежде.
    _MAX_RETRIES = 3
    _RETRY_STATUSES = frozenset({500, 502, 503, 504})
    _IDEMPOTENT_METHODS = frozenset({"GET", "POST"})
    _BACKOFF_BASE = 0.5
    _BACKOFF_CAP = 8.0

    def _req(
        self, method: str, path: str, json: Any = None, params: dict | None = None,
        raw: bool = False, files: Any = None,
    ) -> Any:
        # `with_headers` lived here for ONE caller, `_paged_list`, which read
        # `x-pagination-total-pages` off the response. VMCP-127 deleted that reader's use of the
        # header (see the block above `_paged_list`), so the parameter went with it rather than
        # staying as an unused branch through the retry loop.
        # files (#137): a MULTIPART form upload (e.g. attach a screenshot) — httpx encodes it as
        # multipart/form-data instead of a JSON body, so it and `json` are mutually exclusive (the
        # upload path always passes json=None). Callers pass file CONTENT as bytes, not a file
        # handle, so a 429 retry below re-encodes the SAME body cleanly (a consumed stream would
        # re-send empty). Only PUT uploads use it, and PUT=create is not retried on 5xx (no dup).
        method = method.upper()
        for attempt in range(self._MAX_RETRIES + 1):
            final = attempt == self._MAX_RETRIES
            try:
                r = self._client.request(method, path, json=json, params=params, files=files)
            except httpx.TransportError:
                # обрыв/таймаут: могло примениться -> ретраим только идемпотентные методы
                if final or method not in self._IDEMPOTENT_METHODS:
                    raise
                time.sleep(self._backoff(attempt))
                continue
            if not final and self._should_retry(method, r.status_code):
                time.sleep(self._backoff(attempt, r.headers.get("Retry-After")))
                continue
            if r.status_code >= 400:
                raise VikunjaError(r.status_code, r.text[:300])
            # raw=True: тело — НЕ JSON (эндпоинт скачивания вложения отдаёт сырые байты
            # файла с content-type/content-disposition), поэтому возвращаем r.content как
            # есть, минуя r.json() (который бы упал на бинарнике). См. download_attachment.
            body = r.content if raw else (r.json() if r.content else None)
            return body
        raise AssertionError("unreachable: the final attempt always returns or raises")

    def _should_retry(self, method: str, status: int) -> bool:
        if status == 429:
            return True  # отклонён до применения — безопасно ретраить любой метод
        return status in self._RETRY_STATUSES and method in self._IDEMPOTENT_METHODS

    def _backoff(self, attempt: int, retry_after: str | None = None) -> float:
        if retry_after:
            try:
                return min(float(retry_after), self._BACKOFF_CAP)
            except ValueError:
                pass
        return min(self._BACKOFF_BASE * (2**attempt), self._BACKOFF_CAP)

    # --- postранично читаемые ПЛОСКИЕ списки (VMCP-108) ---
    #
    # THE FOURTH MEMBER OF THE 543/548/562 FAMILY: A TRUNCATED READ TAKEN AS COMPLETENESS. Every
    # list GET in this client except `view_tasks` used to be a SINGLE request, and the server
    # paginates them. MEASURED against a real Vikunja 2.3.0 with max_items_per_page=5, 8-11 rows
    # behind each endpoint:
    #     GET /projects                      -> 5 rows, ?page=2 -> 4 MORE      PAGINATES
    #     GET /labels                        -> 5 rows, ?page=2 -> 3 MORE      PAGINATES
    #     GET /tasks/{id}/comments           -> 5 rows, ?page=2 -> 3 MORE      PAGINATES
    #     GET /projects/{id}/users           -> 5 rows, ?page=2 -> 3 MORE      PAGINATES
    #     GET /projects/{id}/views           -> ALL 10 rows, ?page= IGNORED
    #     GET /projects/{id}/views/{v}/buckets -> ALL 11 rows, ?page= IGNORED
    #
    # Those row counts are that instance's CONTENT, not endpoint constants — VMCP-124 (603)
    # measured 4 views and 63 buckets on another 2.3.0 container also stating
    # max_items_per_page=5. It also measured /projects
    # OVER-SERVING on this same version: the one user measured had BOTH saved filters and a
    # favourite, and got 3 pseudo rows that are not counted against the page size, so at
    # max_items_per_page=5 every page carrying a full window of real rows carried 8 while the real
    # ids paged honestly (the last page with real rows carried 7). "Over-serves" and "ignores
    # `?page=`" are therefore INDEPENDENT properties of an endpoint, not two names for one set.
    # What over-serving COST the degraded read is history, and the past tense is the whole of this
    # correction: it used to widen the degraded loss band (the w-table in the module-level
    # «VMCP-127 (608) — THE FULLNESS INFERENCE IS GONE» block), and 608 deleted the inference both
    # bands came from. MEASURED here (real httpx over MockTransport, real api.py, /info stating 5
    # against a list of 19 rows served 8, 8, 3): 19 rows in 4 requests with /info up and 19 in 4
    # with it down — identical, and the same pair the honest 5,5,2 shape gives (12 in 4 both ways).
    # The note above `_page_size` is still where the /info split is written
    # out, and what survives of it is the CEILING, not this.
    #
    # The four that paginate all fail in the WORSE direction — absence, which every caller acts
    # on. MEASURED end to end: `setup_cmd.reconcile` looks a project up by title over
    # `projects()`, so an EXISTING project past the window reads as missing and reconcile CREATES
    # A DUPLICATE PROJECT (existing id 8 'p6' ignored, new id 18 created — the card's harm, and
    # not something an agent can undo on a real tracker). The other three are the same shape:
    # `get_or_create_label` mints a SECOND label beside the canonical one (the incident its own
    # docstring records, now reachable without anyone typing "Bug " at all); `share_project`
    # re-PUTs a user who is already shared and gets 409 "This user already has access", which
    # ABORTS `setup`; and `comments()` is the worst of them because page 1 holds the OLDEST rows,
    # so a short read drops the NEWEST — the reviewer's `[review]` verdict, the `[worklog]`
    # next_task's review offering keys off (no worklog visible => the card is never offered for
    # review at all), and the human's answer on a card coming back from Your Call.
    #
    # `views()`/`buckets()` are routed through here TOO, though 2.3.0 demonstrably serves them
    # whole. "This endpoint does not paginate in the version I measured" is precisely the
    # assumption that rots — and it rots silently, into duplicate BUCKETS out of the same
    # reconcile and a "project has no kanban view" on a project that has one. The cost is one
    # bounded extra request (they answer ?page=2 with the same rows, so the read stops on
    # `added_new` after exactly 2), on two reads that `Workflow` caches per instance anyway. The
    # uniform rule — every list GET in this client pages — is worth more than the request: it is
    # what stops the next reader having to re-derive which endpoints are safe.
    #
    # THE STOP RULE IS `view_tasks`' — ONE SENTENCE SINCE VMCP-127 (608): keep reading while the
    # page ADDED SOMETHING NEW. That is what TERMINATES the loop and the only thing that can, since
    # the row set is finite and a read that must add a row per page cannot outrun it; it is also
    # what makes a `?page=`-ignoring endpoint cost 2 requests instead of looping on its own repeats
    # (measured: views/buckets stop there, with no duplicate rows).
    #
    # The two conjuncts it USED to carry are gone together, and the measurement lives in the
    # module-level `VMCP-127 (608) — THE FULLNESS INFERENCE IS GONE` block: the fullness half
    # (`len >= min(stated, longest served)`) was an unsound inference that truncated this reader on
    # a short non-final REPEAT window, and the `x-pagination-total-pages` half existed to catch
    # exactly the shape the fullness half could not — so with the inference gone the header could
    # no longer change an outcome, and it went rather than staying as inert code.
    #
    # NOT chosen, and still not: paging authoritatively by `x-pagination-total-pages` (what
    # VMCP-108 suggested, on the strength of the header being meaningful for /projects). It is
    # meaningful for /projects — and on the SAME server it over-reports for views/buckets and
    # under-reports for the kanban tasks endpoint, so "this header is authoritative" is a
    # per-endpoint fact that nothing in the client can check. A stop rule that is right on the
    # endpoints someone measured and silently lossy on the rest is the bug that family of cards is
    # about, one level up.
    #
    # And as in `view_tasks`, hitting the ceiling RAISES rather than returning what it has. A
    # truncated list is indistinguishable from rows that are genuinely gone, and absence is what
    # the callers act on.
    def _paged_list(self, path: str, params: dict | None = None) -> list:
        page_size = self._page_size()   # None = the server never told us; see VMCP-89
        merged: list = []
        seen: set = set()
        unproven_pages = 0              # pages `max_items_per_page` did NOT account for
        page = 1
        while True:
            if unproven_pages >= _MAX_UNPROVEN_PAGES:
                stated = (
                    "This client could not read max_items_per_page from /info, so it cannot tell "
                    "a full page from a short one at all."
                    if page_size is None else
                    f"/info states max_items_per_page={page_size}, but no page ever reached it, "
                    f"and a page SHORT of the stated size is no proof the list is exhausted "
                    f"(VMCP-103) — so the stated size bounds nothing here."
                )
                raise VikunjaError(508, (
                    f"the list at {path} never finished paging: {_MAX_UNPROVEN_PAGES} requests "
                    f"that the server's own page size did not account for, and it was STILL "
                    f"adding rows. {stated} NOTHING is returned rather than a partial list: a "
                    f"truncated list is indistinguishable from rows that are genuinely gone, and "
                    f"this client's callers act on ABSENCE — `setup` creates a duplicate project "
                    f"it could not see, get_or_create_label mints a duplicate label, and a short "
                    f"comment read hides the newest report on a card (VMCP-108). Fix /info so it "
                    f"reports max_items_per_page — pages that FILL it cost this budget nothing — "
                    f"or fix the endpoint's `?page=` if it never converges."
                ))
            body = self._req("GET", path, params={**(params or {}), "page": page})
            # A 200 whose body is not a list is read as NO ROWS, not as an error, because that is
            # how an empty list actually arrives: `_req` returns None for an empty body, and a Go
            # nil slice marshals to `null` (`view_tasks` normalizes the same way, `... or []`).
            # The normalization is load-bearing, not defensive — MEASURED with `items = body`: a
            # page `{"message": ...}` is truthy, so the loop below walks the dict's KEYS and merges
            # the string "message" into the result as a row (VMCP-116).
            items = body if isinstance(body, list) else []
            # AN EARLY-OUT, AND ONLY THAT — an empty page brings no new row, so the stop rule at
            # the bottom would end the read on the very next line anyway. MEASURED (VMCP-116, real
            # httpx over the card's exact shape — page1=5 rows, page2=[], page3=5 rows, every
            # response stating 3 pages): delete these two lines and the answer is unchanged, 5 rows
            # in 2 requests, whole unit suite green. The rule this is a fast path for is broader
            # than "empty" anyway — a page of pure REPEATS adds nothing either, and that is what
            # stops a `?page=`-ignoring endpoint after 2 requests.
            #
            # KEPT DELIBERATELY, as the flat twin of view_tasks' choice (VMCP-103's
            # test_a_page_filtered_down_to_nothing_still_ends_the_read): an all-filtered window and
            # an exhausted list are the same observation, and offset pagination over a stable list
            # makes the empty page the NORMAL terminating shape. VMCP-116's option (b) — let the
            # `x-pagination-total-pages` header carry the read PAST a page that added nothing — was
            # refused as a design and stays refused now that the header is gone entirely: MEASURED
            # on the views/buckets shape 2.3.0 really serves (whole list every page, `?page=`
            # ignored, total-pages OVER-reported), it runs to the server's own page count, 41+
            # requests where this reader spends 2, with the unproven-page ceiling never firing
            # because every page is FULL.
            if not items:
                break
            added_new = False
            for item in items:
                # every list endpoint here returns objects with an `id`; the repr fallback only
                # has to make a REPEATED row compare equal to itself, so that a `?page=`-ignoring
                # server still terminates on `added_new` rather than looping on its own echo.
                key = item["id"] if isinstance(item, dict) and "id" in item else repr(item)
                if key in seen:
                    continue
                seen.add(key)
                merged.append(item)     # first-seen order kept: comments are read positionally
                added_new = True
            if page_size is None or len(items) < page_size:
                unproven_pages += 1     # not justified by the rate the server advertised
            if not added_new:
                break
            page += 1
        return merged

    # --- identity ---
    def me(self) -> dict:
        return self._req("GET", "/user")

    # --- tasks ---
    def get_task(self, task_id: int) -> dict:
        return self._req("GET", f"/tasks/{task_id}")

    def update_task(self, task_id: int, **fields: Any) -> dict:
        current = self.get_task(task_id)
        current.update(fields)
        return self._req("POST", f"/tasks/{task_id}", json=current)

    def create_task(
        self, project_id: int, title: str, description: str = "", priority: int = 0
    ) -> dict:
        return self._req(
            "PUT", f"/projects/{project_id}/tasks",
            json={"title": title, "description": description, "priority": priority},
        )

    # --- attachments ---
    # Task attachments arrive INSIDE the task JSON under the existing tasks:read_one scope
    # (task["attachments"] = [{id, task_id, file:{id,name,mime,size}, ...}], or None when the
    # task has none — verified on real 2.3.0), so listing metadata needs no extra call. Only
    # DOWNLOADING the bytes hits a separate endpoint and needs the tasks_attachments:read scope.
    def download_attachment(self, task_id: int, attachment_id: int) -> bytes:
        """Raw bytes of a task attachment. `attachment_id` is the attachment's OWN id
        (task["attachments"][].id, surfaced by workflow.get_task), NOT the nested file.id.
        GET /tasks/{id}/attachments/{attachment_id} streams the file itself, not JSON, so it
        goes through _req(raw=True) — same GET retry/backoff, but the body is returned
        verbatim. Needs the tasks_attachments:read token scope; a wrong task or attachment id
        surfaces as VikunjaError(404)."""
        return self._req("GET", f"/tasks/{task_id}/attachments/{attachment_id}", raw=True)

    def upload_attachment(
        self, task_id: int, filename: str, data: bytes, mime: str | None = None
    ) -> dict:
        """Upload bytes as a task attachment (e.g. a screenshot of finished work). The endpoint is
        PUT /tasks/{id}/attachments and takes a MULTIPART form — file field `files` — NOT a JSON
        body, so it goes through _req(files=...): the upload-side twin of download_attachment's
        raw=True on the response side (api.py's JSON helpers don't fit either end). Verified on
        real 2.3.0: the governing scope is `tasks_attachments:create` (401 without it), the method
        is PUT (POST -> 405), and the response is
        {"errors": ..., "success": [{id, task_id, file:{id,name,mime,size,...}, ...}]}. `data` is
        bytes (not a stream) so a 429 retry re-encodes the same body; PUT=create is not retried on
        5xx, so an ambiguous failure can't duplicate the upload."""
        file_part = (filename, data, mime) if mime else (filename, data)
        return self._req("PUT", f"/tasks/{task_id}/attachments", files={"files": file_part})

    # --- comments ---
    def comments(self, task_id: int) -> list[dict]:
        # PAGED (VMCP-108): page 1 holds the OLDEST comments, so a single-request read dropped
        # the NEWEST — the [review] verdict, the [worklog] next_task's review offering requires,
        # and a human's answer to call_human. Order across pages is preserved.
        return self._paged_list(f"/tasks/{task_id}/comments")

    def add_comment(self, task_id: int, text: str) -> dict:
        # Vikunja's comment field is HTML (#85): agents author plain text with newlines,
        # so convert to structure-preserving, HTML-escaped HTML at this single chokepoint
        # — every agent comment body (comment/spec/worklog/review/call_human/claim/...)
        # passes through here.
        return self._req(
            "PUT", f"/tasks/{task_id}/comments", json={"comment": text_to_html(text)}
        )

    # --- assignees ---
    def add_assignee(self, task_id: int, user_id: int) -> None:
        self._req("PUT", f"/tasks/{task_id}/assignees", json={"user_id": user_id})

    def remove_assignee(self, task_id: int, user_id: int) -> None:
        self._req("DELETE", f"/tasks/{task_id}/assignees/{user_id}")

    # --- relations ---
    def add_relation(self, task_id: int, other_task_id: int, kind: str) -> None:
        self._req(
            "PUT", f"/tasks/{task_id}/relations",
            json={"other_task_id": other_task_id, "relation_kind": kind},
        )

    # --- projects ---
    def projects(self) -> list[dict]:
        # PAGED (VMCP-108): the card's own endpoint. A single request stopped at
        # max_items_per_page, so an EXISTING project past the window read as absent and
        # setup_cmd.reconcile created a DUPLICATE.
        return [p for p in self._paged_list("/projects") if p.get("id", 0) > 0]

    def create_project(self, title: str) -> dict:
        return self._req("PUT", "/projects", json={"title": title})

    def project_users(self, project_id: int) -> list[dict]:
        # PAGED (VMCP-108): share_project's "is this user already shared?" check reads this, and
        # a share hidden past page 1 makes it re-PUT — Vikunja answers 409 "This user already
        # has access to this project", which aborts `setup` outright.
        return self._paged_list(f"/projects/{project_id}/users")

    def share_project(self, project_id: int, username: str, permission: int) -> None:
        for share in self.project_users(project_id):
            if share.get("username") == username:
                return
        self._req(
            "PUT", f"/projects/{project_id}/users",
            json={"username": username, "permission": permission},
        )

    # --- views & buckets ---
    def views(self, project_id: int) -> list[dict]:
        # PAGED (VMCP-108) although 2.3.0 serves this whole and ignores ?page= — see _paged_list
        # for why measured-not-to-paginate is not a licence to single-shot it. A short read here
        # would surface as `kanban_view` raising "project has no kanban view" on a project that
        # has one, i.e. as a board that cannot be reconciled.
        return self._paged_list(f"/projects/{project_id}/views")

    def kanban_view(self, project_id: int) -> dict:
        for v in self.views(project_id):
            if v["view_kind"] == "kanban":
                return v
        raise VikunjaError(404, "project has no kanban view — run `vikunja-mcp setup`")

    def buckets(self, project_id: int, view_id: int) -> list[dict]:
        # PAGED (VMCP-108) although 2.3.0 serves this whole and ignores ?page= — same reasoning
        # as views(). A short read here is the duplicate-project harm one level down: reconcile
        # builds {title: bucket} from this and CREATES every canonical column it cannot see.
        return self._paged_list(f"/projects/{project_id}/views/{view_id}/buckets")

    def create_bucket(self, project_id: int, view_id: int, title: str) -> dict:
        return self._req(
            "PUT", f"/projects/{project_id}/views/{view_id}/buckets", json={"title": title}
        )

    def delete_bucket(self, project_id: int, view_id: int, bucket_id: int) -> None:
        self._req("DELETE", f"/projects/{project_id}/views/{view_id}/buckets/{bucket_id}")

    def update_bucket(
        self, project_id: int, view_id: int, bucket: dict, position: float
    ) -> dict:
        # full-replace бакета: шлём title + position, порядок колонок = position
        return self._req(
            "POST", f"/projects/{project_id}/views/{view_id}/buckets/{bucket['id']}",
            json={"title": bucket["title"], "position": position},
        )

    # эмпирически против vikunja 2.3.0 (см. отчёт F1): GET .../views/{v}/tasks пагинирует
    # tasks[] ВНУТРИ каждого бакета независимо через params={"page": n} с фиксированным
    # page size = max_items_per_page сервера (per_page на эту вложенную пагинацию не влияет).
    # Заявленный размер читаем из /info (_page_size, кэш на клиенте) — но ЗДЕСЬ ОН БЮДЖЕТ, А НЕ
    # ОРАКУЛ: КОРОТКАЯ страница больше не значит «бакет кончился» (VMCP-127 (608) убрал этот
    # вывод из ОБОИХ ридеров; направление легко перевернуть, поэтому явно: останавливала чтение
    # КОРОТКАЯ страница, полная его ПРОДОЛЖАЛА). В этом цикле заявленный размер отвечает не на
    # «кончился ли бакет», а только на «сколько ещё страниц этому чтению позволено потратить»:
    # страница, на которой требуемый бакет отдал НЕ МЕНЬШЕ заявленного, потолку ничего не стоит
    # (`stated_full_required` -> `unproven_pages` -> `_MAX_UNPROVEN_PAGES`; подробно — в абзаце
    # «AND THE STATED PAGE SIZE STILL EARNS ITS KEEP — AS A BUDGET, NOT AS AN ORACLE» этой же
    # заметки). Само правило остановки заявленный размер не спрашивает вовсе, и здесь оно НЕ
    # пересказывается: описано там, где живёт, — у `keep_going` в цикле view_tasks и в модульном
    # блоке «VMCP-127 (608) — THE FULLNESS INFERENCE IS GONE FROM BOTH READERS» (ищи по
    # заголовку: файл длинный). Но «не спрашивает» — это про то, ГДЕ сработает keep_going, а НЕ
    # про исход чтения целиком: через потолок /info на исход влияет, и ветки расходятся ровно на
    # `_MAX_UNPROVEN_PAGES` неоправданных страниц. ИЗМЕРЕНО (real httpx over MockTransport, real
    # api.py): 119 полных страниц и пустая — обе ветки отдают 595 задач за 120 запросов; 120
    # полных и пустая — живой /info отдаёт 600 за 121 запрос, а упавший ПАДАЕТ 508 после 120.
    # Страницы могут перекрываться на 1-2 задачи из-за нестабильной сортировки при равных
    # ключах (без ORDER BY тайбрейкера) — наблюдался дубль, ни разу не пропуск. Мёржим по
    # (bucket_id, task_id), а перед возвратом дедупим ещё раз ГЛОБАЛЬНО по task id, оставляя
    # задачу только в последнем бакете, где её видели (#41, сразу после цикла): в возвращённой
    # доске каждая задача встречается ровно один раз.
    #
    # VMCP-89 — THE PAGE SIZE IS KNOWN OR UNKNOWN, NEVER GUESSED, and that is a data-loss fix.
    # `_fetch_page_size` used to swallow an unreachable or silent `/info` and return a hardcoded
    # 50. On an instance whose real max_items_per_page is SMALLER, no bucket ever returns 50
    # tasks, so "no bucket gave a full page" read as "that is the whole board" and this loop
    # TRUNCATED after page 1 — no exception, no marker, just fewer tasks. `workspace --gc` builds
    # its liveness set from exactly this board and destroys the worktree of every task missing
    # from it. CONSTRUCTED (real git worktrees, real httpx, real api.py, max_items_per_page=3, a
    # 500 on /info, five live Build tasks): `released=[804, 805]` — two LIVE trees removed and
    # their `task/*` branches deleted, in a sweep that reported success. gc is the caller that
    # loses work, but it is not the only victim: claim/_find_task read a truncated board as "no
    # such task" on a card that is right there.
    #
    # So the guess is gone. `_page_size()` answers None when the server never told us — EITHER
    # way it can fail to (the request errored, or the payload carries no usable
    # max_items_per_page) — and an UNKNOWN size simply forbids concluding a bucket is complete
    # from a SHORT page: the loop then keeps going until a page brings no NEW task in the required
    # buckets, which is a fact about the DATA and needs no page size at all. AS VMCP-89 LEFT IT
    # that cost exactly one extra request and only while /info was broken, because a KNOWN size
    # still kept the cheap fullness rule and the healthy path paid nothing. THAT SPLIT IS HISTORY:
    # VMCP-127 (608) deleted the fullness rule from the HEALTHY branch too, so neither branch
    # concludes anything from a page's LENGTH any more, and the two spend the SAME requests on the
    # same board — MEASURED on an honest 5,5,2 board (real httpx over MockTransport, real api.py):
    # 12 tasks in 4 requests with /info up, and 12 in 4 with /info down. BELOW THE CEILING, that
    # is: `_MAX_UNPROVEN_PAGES` is now the only thing that still makes the two branches READ
    # differently, and it does so loudly rather than silently (the numbers are in the header
    # paragraph of this note, under «ЗДЕСЬ ОН БЮДЖЕТ»). What survives untouched is
    # #43's require_titles win — an exhausted Queue beside a Done handing out five brand-new tasks
    # every page still stops at ONE request on BOTH branches (measured the same way), because
    # required buckets that come back EMPTY end the read whatever /info said; see «THE COST IS REAL
    # AND IS NOT HIDDEN» in this note. Both outcomes are resolved ONCE per client
    # (`_page_size_resolved`), so a broken /info does not add a probe per call either.
    #
    # NOT chosen: making the failure fail-CLOSED (propagate, so gc abandons the sweep — the shape
    # VMCP-72 used for its read deadline). It keeps gc at KEEP, but it leaves the identical
    # truncation live for next_task/claim/setup, and it would disable housekeeping FOREVER on a
    # deployment whose /info simply does not report the field. Not guessing is strictly stronger
    # than refusing to act on a guess: the read stays CORRECT instead of merely being abandoned.
    #
    # VMCP-92 — THE DEGRADED BRANCH IS BOUNDED, AND IT RAISES RATHER THAN RETURNING A SHORT BOARD.
    # VMCP-89 left one residual on this branch only. A KNOWN page size bounds the request count at
    # ceil(N/page_size)+1 whatever the server does; an UNKNOWN one had NO bound — the loop ran for
    # as long as any page brought a new required task. CONSTRUCTED (real httpx, real api.py): a
    # server handing out one brand-new Build task per page never terminated (401 requests and
    # still going when the harness cut it off). The contained caller was fine — `--gc` abandons
    # this read on VMCP-72's 30 s deadline, and `ReadDeadlineExceeded` is a WorkspaceError so
    # `_fetch_page_size` cannot swallow it — but next_task/claim/advance/setup have no deadline
    # and would hang forever.
    #
    # So this branch now (a) issues at most `_MAX_UNPROVEN_PAGES` requests and (b) on
    # hitting that ceiling RAISES, returning nothing. Raising is the whole point, not a detail: a
    # truncated board is indistinguishable from one whose tasks are genuinely gone, and that
    # indistinguishability is what ends in `--gc` reaping a LIVE worktree. Every caller's failure
    # direction here is KEEP or no-op — gc propagates it out of `_read_liveness` before the reap
    # loop is ever entered, `server._tool` turns it into `{"error": ...}` instead of a hung tool
    # call, `setup` refuses a reconcile it cannot base on a complete board.
    #
    # AND THE STOP RULE STOPPED CONCLUDING COMPLETENESS FROM A REPEAT (the second half of VMCP-92)
    # — the one clause of that card still standing. "A page brought no new required task" was the
    # ONLY stop here, which is strictly weaker in one shape: a required bucket that re-serves a
    # window of already-seen tasks while some other bucket still adds new ones. MEASURED on that
    # exact server: a known size read on and got Build[1..6], an unknown size stopped at
    # Build[1,2,3] — the same silent truncation VMCP-89 exists to remove. So the loop also
    # continues while a REQUIRED bucket came back with tasks AT ALL and something new arrived
    # anywhere on the page.
    #
    # THAT CLAUSE USED TO CARRY A LENGTH TEST, AND VMCP-127 (608) DELETED IT. It read "...came back
    # with a page at least as long as min(size STATED by /info, longest page SERVED)", and
    # VMCP-89/92/103/111/124 are five cards spent on that one threshold. It was an unsound
    # inference — a page short of the server's real page size can still have tasks behind it — and
    # both /info branches spelled the same one, so an over-serving server truncated BOTH of them.
    # The module-level `VMCP-127 (608) — THE FULLNESS INFERENCE IS GONE` block carries the w-table
    # that measures it, the control that names the trigger (a short non-final REPEAT window,
    # over-serving or not) and the price of removing it. What is left over-reads rather than
    # under-reads: dropping a guard from a keep-going can only make it fire
    # more often, so this rule is a strict superset of the one it
    # replaced on EVERY server. And it consults no page size at all, so /info being up or down
    # can no longer change where this read stops BELOW THE CEILING — the property VMCP-103 was for
    # and VMCP-124 (603) found broken, now structural instead of argued. That qualifier carries the
    # whole sentence and was missing from it: the stop RULE ignores the stated size,
    # `_MAX_UNPROVEN_PAGES` does not, so a degraded read CAN raise where a healthy one reads on.
    # Only can: the ceiling charges a page only when it is SHORT of the stated size, so a shape
    # whose pages never reach that size spends the budget identically on both branches and 508s on
    # the same request. The measured pair is a few paragraphs up, under «ЗДЕСЬ ОН БЮДЖЕТ».
    #
    # VMCP-103 — WHY THE TWO BRANCHES HAD TO BE UNIFIED, kept because a deleted threshold is not
    # the only way to re-split them. VMCP-89/92 spent two cards deleting "a short page proves the
    # bucket is complete" from the DEGRADED branch and left it standing on the healthy one, where
    # `saw_full_page` took a page SHORT of the STATED size as proof of exhaustion. MEASURED (real
    # httpx, real api.py): /info stating max_items_per_page=5 and one required Build serving
    # page1=[1,2] (short by accident), page2=[3,4,5,6,7], page3=[8,9] — the HEALTHY read stopped
    # after ONE request with Build[1,2], silently losing 3..9, while the DEGRADED read spent 4
    # requests and returned Build[1..9] whole. Backwards, and invisible to both parity sweeps,
    # which only ever modelled honest servers whose pages are full until the last one. A branch
    # that makes the read BETTER when /info is broken is the shape to refuse; there is now no
    # branch left to make it in.
    #
    # A server that ignores `?page=` entirely still stops after two requests, exactly as it did
    # before all of this: its repeat brings nothing new ANYWHERE, and this clause needs a new task
    # somewhere to run on (re-measured against a live 2.3.0 on 2026-07-31 — views/buckets serve the
    # whole list every page and both readers stop at 2).
    #
    # HOW CLOSE THE TRIGGER WAS — VMCP-124 (603)'s measurement, kept because it is the EVIDENCE
    # FOR the deletion rather than a description of the deleted rule. Read it in the past tense:
    # every "the degraded read stops a page earlier" below is a statement about the bar VMCP-127
    # removed, and on the current tree both /info branches read those same shapes identically.
    #
    # HOW CLOSE IS THE TRIGGER? CLOSER THAN "UNOBSERVED", AND THE REASON THIS COMMENT USED TO GIVE
    # IS MEASURED FALSE. "The trigger" here means specifically the DIVERGENCE this card is named
    # after — the degraded read losing a row the healthy read saw — which is narrower than
    # truncation as such (the control in the VMCP-127 block above truncates the healthy read with no
    # over-serving at all). That divergence needed over-serving AND a later page SHORT of the
    # degraded bar with rows still behind it, in the SAME read — the longest served length was a
    # per-call local, so one endpoint's long page never leaked into another read. The claim here was
    # that the endpoints which over-serve are PRECISELY the ones that ignore `?page=`, so their next
    # page is always a pure repeat that ends the read on `added_new`. That is false, and the
    # counter-example is this client's own reconcile read. On a live 2.3.0 container (2026-07-31,
    # stated size 5) with 34 projects, 2 saved filters and a favourite, the pseudo-projects
    # (Favorites, plus one row per saved filter) are simply not counted against the page size —
    # which is the OBSERVATION; where the server applies its limit relative to them is its own
    # business and was not measured:
    #
    #     GET /projects?page=1..6  -> 8 rows each (5 real + a CONSTANT 3-row pseudo tail: -1
    #                                 Favorites, -2 and -3 the saved filters), the real ids
    #                                 ADVANCING 1-5, 6-10 … 26-30; total-pages 7
    #     GET /projects?page=7     -> 7 rows (4 real, 31-34, + the tail) — still 7 > the stated 5
    #     GET /projects?page=8..12 -> 3 rows each (the tail alone), i.e. SHORT of the stated 5
    #
    # So it over-serves on every page that carries a FULL window of real rows, and NOT on every
    # page — that last table row is why. Nor is "carries real rows at all" the boundary, which is
    # the tempting next over-claim and is MEASURED FALSE: what a page hands back is (real rows in
    # the window) + (the tail), so with THIS user's tail of 3 against a stated 5 it exceeds the
    # stated size only from three real rows up — measured at 1 real (4 rows, under), 2 real (5
    # rows, exactly the stated size and so still not over) and 3 real (6 rows, over). The tail is
    # a property of the USER's pseudo-project set, not a constant 3. Re-probed on the same
    # container as it grew, all at a stated 5:
    #
    #     38 real -> pages 1-7 serve 8 (5 real), page 8 serves 6 (3 real, still over), page 9
    #                onward the tail alone at 3; those tail-only pages are sha256-IDENTICAL to
    #                one another (page 8 is not), so past the last real row nothing new arrives
    #     41 real -> pages 1-8 serve 8, page 9 serves FOUR (1 real + the tail) — UNDER the stated
    #                5, so the last page carrying real rows need not over-serve at all
    #
    # The page NUMBERS above are that instance's CONTENT; the shape is the endpoint's. And it does
    # all this WHILE paging honestly: the page after an over-serving one ADVANCES the real ids
    # instead of repeating them, which is the whole counter-example. Only the tail-only pages past
    # the last real row repeat — those are the sha256-identical ones — so "over-serves" and
    # "ignores `?page=`" really do come apart here.
    # Through this client against that container, /info up vs /info down:
    #
    #     projects()   34 rows /  8 req  vs  34 rows /  7 req    <- the ONLY one that differs
    #     labels()     66 rows / 14 req  vs  66 rows / 14 req
    #     views()       4 rows /  2 req  vs   4 rows /  2 req
    #     buckets()    63 rows /  2 req  vs  63 rows /  2 req
    #     view_tasks() 37 tasks / 9 req  vs  37 tasks / 9 req
    #
    # The row counts there are the container's CONTENT and drifted between two runs of the probe
    # (labels was 22 rows / 5 req and view_tasks 13 tasks / 4 req before it was topped up); what
    # did NOT drift, in either run, is which reader disagrees across the two /info states — only
    # `projects()`. The degraded read really does stop a page earlier on a real endpoint, by
    # exactly this bar (page 7 serves 7 < the degraded bar 8, while the healthy bar min(5,8)=5
    # lets it read on).
    #
    # NOTHING IS LOST THERE, and the reason has to be named precisely because it is not the reason
    # given above. On this shape the pseudo tail is a CONSTANT 3 rows, so the page short of the bar
    # is the LAST one carrying real rows — the read is over anyway. The header is NOT what saved
    # it here (it says 7 of 7, i.e. it agrees the read is done); what saved it is that there is
    # nothing behind the short page. Move that page off the end and the loss appears — CONSTRUCTED
    # over this exact shape (5 real + 3 pseudo per page, page 3 serving only 2 real, page 4 still
    # holding 13-17): with the header present both reads returned all 17 rows in 5 requests, but
    # with the header ABSENT the degraded read returned 1..12 in 3 requests while the healthy one
    # still returned all 17. So on that constructed shape it was `x-pagination-total-pages` —
    # reporting a page PAST the short one — that kept the degraded read whole. Which was a thin
    # thread to hang on: the same header the "WHAT WENT WITH IT" paragraph of the VMCP-127 block
    # records as measured WRONG in both directions on this very version, on other endpoints.
    # VMCP-127 cut the thread from the other end — RE-MEASURED on this tree, that constructed shape
    # now returns all 17 rows in 5 requests in ALL FOUR combinations of header present/absent and
    # /info up/down, and no pagination header is read anywhere (the one live `r.headers` in this
    # file is `Retry-After`).
    #
    # WHAT WAS TRIED AND DID NOT PRODUCE THE LOSS on 2.3.0, recorded so nobody redoes it:
    # permission-filtered /labels and /projects for a second user (for the ONE filtered user
    # measured, row count and total-pages both described the FILTERED set — labels an EMPTY page 1
    # at 0 pages, projects 1 row at 1 page — so no short non-final page appeared);
    # 63 buckets, to see whether that endpoint caps internally and would carry a short page later
    # (no cap appeared at 63 — 63 rows on page 1 and the identical 63 on page 2); and the
    # NESTED endpoint itself, whose per-bucket windows never EXCEEDED the stated 5 in either
    # re-measured run (2026-07-31: 5,5,1,0 over a bucket holding 11 tasks, and seven windows of 5
    # then 0 over one holding 34 — seven fives over 34 rather than 35 because one of those windows
    # repeats a task, the unstable-sort overlap this file documents at `view_tasks`; and both task
    # counts are what the container ACTUALLY held, not what the probe asked it to create), so it
    # does not over-serve and those long flat pages cannot leak in. A short non-final page could
    # not be produced on the kanban tasks endpoint at all (see "HONEST ABOUT THE TRIGGER" below).
    #
    # THE COST IS REAL AND IS NOT HIDDEN: one extra request per `view_tasks` call whenever a
    # required bucket brought a NEW task on the last page that had content — the smallest board
    # goes from 1 request to 2 (~0.25 s at the rate measured below). It is unavoidable rather than
    # sloppy: a short page and a filtered page are the same observation, and only asking for one
    # more page tells them apart. It is bounded and FLAT — one page per READ, not per bucket — and
    # it is not paid at all when the required buckets come back EMPTY, which is what keeps #43's
    # require_titles win intact (an exhausted Queue beside an unbounded Done still stops at page 1).
    #
    # AND THE STATED PAGE SIZE STILL EARNS ITS KEEP — AS A BUDGET, NOT AS AN ORACLE. Since
    # VMCP-127 that is the ONLY thing it does in this reader: it no longer answers "is this bucket
    # finished?", only "how many more pages may this read spend?". Nothing
    # bounds `added_new_required` on its own, so the ceiling VMCP-92 gave the degraded read now
    # covers both, counted over the pages the STATED size did NOT justify: a page on which some
    # required bucket came back full at `max_items_per_page` is the server delivering at the rate
    # it advertised, and it costs nothing. An honest 8 000-task board therefore still reads whole
    # in 161 requests (161 justified pages), while a server that never fills a page is cut off at
    # `_MAX_UNPROVEN_PAGES` — and cut off by RAISING, never by returning the short board.
    #
    # NOT chosen: (a) `x-pagination-total-pages` as a free stop signal — MEASURED against a real
    # Vikunja 2.3.0: on this endpoint those headers describe the BUCKET list (result-count 3,
    # total-pages 1) while the bucket behind them held 3 pages of tasks, so the header is not
    # merely useless here, it is WRONG. (b) A `strict=` flag so that only `--gc` pays the extra
    # page: that moves the asymmetry from "/info up vs down" to "caller remembered vs forgot", and
    # `_find_task`/claim read a truncated board as "no such task" on a card that is right there.
    # (c) The flat ceiling on both branches: an honest 8 000-task board would become an error, and
    # `require_titles=None` (claim/setup) makes an ever-growing Done a required bucket. (d) A
    # progress bound (pages <= ceil(seen/page_size)+K): it would break exactly the
    # filter-after-paginate shape it exists to survive, filtering being slow delivery relative to
    # the window.
    #
    # HONEST ABOUT THE TRIGGER: a short non-final page could NOT be produced ON THIS ENDPOINT
    # against Vikunja 2.3.0 — and the scope word buys less than it looks like, because the FLAT
    # side does not produce the trigger either. /projects does serve pages short of the stated size
    # that are not the last one the server answers (measured 2026-07-31 at stated 5: with 41 real
    # projects page 9 serves FOUR rows, and pages 10 onward serve the 3-row pseudo tail alone —
    # WHICH page numbers those are, is the instance's content, not the endpoint's). None of them is
    # this module's trigger, which needs NEW ROWS behind the short page: the tail-only pages are
    # sha256-identical to one another, so nothing new ever arrives and the read ends on
    # `added_new`. What the short page 9 has behind it is more pages, not more rows.
    # Request-level `filter=`, a saved filter on the view, `s=` search, and done tasks auto-moving
    # into the Done bucket all push the filter into SQL, so every page comes back full until the
    # last (probed on a container with max_items_per_page=5). The mechanism the card suspected —
    # paginate the unfiltered set, filter afterwards — is NOT reproduced here, which is not the
    # same as proven impossible (a proxy, a later version, or a Typesense-backed search hydrating
    # ids from the DB would all have that shape). Re-probed by VMCP-124 (603) and again here: still
    # not producible, and permission-filtered /labels and /projects filter in SQL too. What never
    # depended on settling it is the rule itself — VMCP-127 removed the inference that a short page
    # ends a read rather than waiting for a server to demonstrate the shape, because the caller
    # this read feeds (`workspace --gc`) turns a truncated board into a REAPED LIVE WORKTREE and
    # that harm is not conditional on which Vikunja version produced the short page.
    def _page_size(self) -> int | None:
        if not self._page_size_resolved:
            self._page_size_cache = self._fetch_page_size()
            self._page_size_resolved = True
        return self._page_size_cache

    def _fetch_page_size(self) -> int | None:
        # /info — публичный, неаутентифицированный эндпоинт; Bearer на нём безвреден.
        # Ошибку по-прежнему ГЛОТАЕМ (у этого резолвера есть вызыватели, которым падение
        # из-за /info было бы хуже деградации) — но отдаём None «не знаю», а не число-догадку:
        # решает не молчание, а то, что с None делает view_tasks (см. комментарий выше).
        try:
            info = self._req("GET", "/info")
        except (VikunjaError, httpx.HTTPError):
            return None
        size = info.get("max_items_per_page") if isinstance(info, dict) else None
        return size if isinstance(size, int) and size > 0 else None

    def view_tasks(
        self, project_id: int, view_id: int, require_titles: set[str] | None = None
    ) -> list[dict]:
        # require_titles (#43): the set of bucket TITLES whose "full page" should keep the
        # pagination loop going. None (default) = every bucket counts -> exhaustive read, kept
        # for _find_task/claim/setup which must see the complete board (incl. a Done task).
        # When given, only those buckets drive paging: an unbounded Done/Backlog that still
        # returns full pages no longer forces extra fetches once the required buckets are
        # exhausted. next_task passes its working stages here so it stops after them instead of
        # rescanning the ever-growing Done on every call (the named next_task-latency fix).
        page_size = self._page_size()       # None = the server never told us; see VMCP-89 above
        merged: dict[int, dict] = {}
        seen: dict[int, set] = {}
        owner: dict[int, int] = {}          # task_id -> последний бакет, где её видели (см. дедуп ниже)
        unproven_pages = 0                  # VMCP-103: pages already spent that `max_items_per_page`
                                            # did NOT account for — the only ones the ceiling counts.
        page = 1
        while True:
            if unproven_pages >= _MAX_UNPROVEN_PAGES:
                # VMCP-92: NOT sent, and NOTHING returned — see the note above `_page_size`. The
                # message has to be self-explaining: it is the only thing a human gets, and the
                # thing it names (/info) is the thing that actually needs fixing.
                #
                # A plain VikunjaError, not a new class: it is what every caller ALREADY handles
                # (`server._tool` -> `{"error": ...}`, the workspace CLI's error line, claimable's
                # exit 1), and a new class is one more thing for an `except` site to miss. The 508
                # (Loop Detected — the server's own paging is what fails to converge) is
                # synthesized the way `kanban_view` synthesizes its 404; it collides with nothing,
                # since the only status-sensitive sites are 403/404 in file_task and 401's token
                # reload, and being raised HERE rather than by `_req` it is never retried.
                stated = (
                    "This client could not read max_items_per_page from /info, so it cannot tell "
                    "a full page from a short one at all."
                    if page_size is None else
                    f"/info states max_items_per_page={page_size}, but no page of a required "
                    f"bucket ever reached it, and a page SHORT of the stated size is no proof "
                    f"that bucket is exhausted (VMCP-103) — so the stated size bounds nothing "
                    f"here."
                )
                raise VikunjaError(508, (
                    f"the board never finished paging: {_MAX_UNPROVEN_PAGES} requests to "
                    f"/projects/{project_id}/views/{view_id}/tasks that the server's own page "
                    f"size did not account for, and it was STILL adding tasks. {stated} NOTHING "
                    f"is returned rather than a partial board: a truncated board is "
                    f"indistinguishable from tasks that are genuinely gone, and `workspace --gc` "
                    f"reaps worktrees from exactly this read (VMCP-89). Fix /info so it reports "
                    f"max_items_per_page — pages that FILL it cost this budget nothing — or fix "
                    f"the view's pagination if it is `?page=` that never converges."
                ))
            buckets = self._req(
                "GET", f"/projects/{project_id}/views/{view_id}/tasks", params={"page": page}
            ) or []
            if not buckets:
                break
            stated_full_required = False
            required_had_tasks = False
            added_new = False
            added_new_required = False
            for bucket in buckets:
                bid = bucket["id"]
                dest = merged.setdefault(bid, {**bucket, "tasks": []})
                ids = seen.setdefault(bid, set())
                tasks = bucket.get("tasks") or []
                required = require_titles is None or bucket.get("title") in require_titles
                if required and tasks:
                    # NOT "and the page looked full": that length test was the inference VMCP-127
                    # deleted; the measurement is in the module-level
                    # `VMCP-127 (608) — THE FULLNESS INFERENCE IS GONE` block. A required bucket
                    # that came back with ANYTHING has not demonstrated it is finished, whatever
                    # /info said about how much it would have served.
                    required_had_tasks = True
                    if page_size is not None and len(tasks) >= page_size:
                        stated_full_required = True     # the server delivered at its OWN stated rate
                for task in tasks:
                    owner[task["id"]] = bid          # последнее вхождение выигрывает (см. дедуп ниже)
                    if task["id"] not in ids:
                        ids.add(task["id"])
                        dest["tasks"].append(task)
                        added_new = True
                        added_new_required = added_new_required or required
            # A page's LENGTH proves NOTHING about a bucket being exhausted — whatever /info said,
            # and VMCP-127 is where this reader stopped pretending otherwise — so the loop stops on
            # facts about the DATA: it keeps going while a page brought a NEW task to a REQUIRED
            # bucket (required-only on purpose: counting any bucket would let an unbounded
            # Done/Backlog drag the loop through itself, the very cost #43's require_titles exists
            # to avoid), and while a required bucket came back with tasks at all beside a page that
            # added something somewhere (VMCP-92's repeat-window edge, which "nothing new in a
            # required bucket" alone misses). No branch on /info any more: the same expression runs
            # whether the page size is known or not — see the long note above `_page_size`.
            #
            # WHICH OF THE TWO DISJUNCTS DECIDES: only the SECOND, and the first is kept as
            # redundancy that is NAMED rather than mistaken for load-bearing (VMCP-144, 633).
            # `added_new_required` is set only inside `if task["id"] not in ids:` for a bucket
            # whose `required` is true — and such a bucket has already passed `if required and
            # tasks` (so `required_had_tasks`) and sets `added_new` on the very next line. All
            # four flags reset per page, so within a page `added_new_required` IMPLIES
            # `required_had_tasks and added_new`, and `A or B` with `A ⇒ B` is just `B`. Deleting
            # it would therefore be behaviour-preserving TODAY; it stays because this is the loop
            # whose truncated answer once let `--gc` reap a live worktree (#543), and a spare term
            # is cheaper here than the edit that would re-derive it. What must not happen is the
            # comment claiming it does work — that is what 633 was filed about. The implication is
            # PINNED (tests/unit/test_api_kanban.py), so an edit that breaks it reddens rather
            # than quietly turning this paragraph into a lie.
            keep_going = added_new_required or (required_had_tasks and added_new)
            if not stated_full_required:
                unproven_pages += 1         # this page was not justified by max_items_per_page
            if not keep_going:
                break
            page += 1
        # #41 глобальный дедуп по task id: задачу, переезжающую между колонками ВО ВРЕМЯ
        # постраничного чтения, мы видим в старом бакете на ранней странице и в новом — на поздней,
        # т.е. дважды. Покомпонентный (bucket_id, task_id) merge выше оба вхождения сохранял, и
        # _find_task (берёт первое) залипал на устаревшей колонке. Оставляем задачу ТОЛЬКО в её
        # последнем бакете: страницы читаются последовательно во времени, поздняя = более свежее
        # наблюдение доски, куда бы задачу ни двигали. После этого прохода каждый task id встречается
        # ровно один раз, поэтому дедуп и _find_task (первое вхождение) согласованы по определению.
        for bid, dest in merged.items():
            dest["tasks"] = [t for t in dest["tasks"] if owner.get(t["id"]) == bid]
        return list(merged.values())

    def move_task(self, project_id: int, view_id: int, bucket_id: int, task_id: int) -> None:
        self._req(
            "POST", f"/projects/{project_id}/views/{view_id}/buckets/{bucket_id}/tasks",
            json={"task_id": task_id},
        )

    def configure_kanban(
        self, project_id: int, view: dict, default_bucket_id: int, done_bucket_id: int
    ) -> dict:
        # full-replace: без mode+position канбан теряет колонки
        return self._req(
            "POST", f"/projects/{project_id}/views/{view['id']}",
            json={
                "title": view["title"],
                "view_kind": "kanban",
                "bucket_configuration_mode": "manual",
                "position": view["position"] if view.get("position") is not None else 400,
                "default_bucket_id": default_bucket_id,
                "done_bucket_id": done_bucket_id,
            },
        )

    # --- labels ---
    def labels(self) -> list[dict]:
        # PAGED (VMCP-108): get_or_create_label scans this to REUSE an existing label. A label
        # hidden past page 1 reads as absent and gets minted a second time — the duplicate its
        # docstring below records as a real incident, reachable here without any typo at all.
        return self._paged_list("/labels")

    def create_label(self, title: str) -> dict:
        return self._req("PUT", "/labels", json={"title": title})

    def add_label(self, task_id: int, label_id: int) -> None:
        self._req("PUT", f"/tasks/{task_id}/labels", json={"label_id": label_id})

    def remove_label(self, task_id: int, label_id: int) -> None:
        self._req("DELETE", f"/tasks/{task_id}/labels/{label_id}")

    def get_or_create_label(self, title: str) -> dict:
        # Vikunja labels are owned per-user; GET /labels surfaces every label used on a
        # task the caller can read (not just its own), so match case- and whitespace-
        # insensitively to REUSE an existing label instead of minting a divergent
        # duplicate.
        # AND IT EXCLUDES THE REST — the sentence above states only the WIDENING half, and the
        # exclusion on top of it was an inference nothing measured until #1456, which measured it
        # against a real 2.3.0 WITH A CONTROL: a row owned by another user and used on no task
        # the caller can read is simply ABSENT from that caller's list, and the
        # same row APPEARS the moment it is put on a task the caller can read (the other caller
        # sees both throughout, so the difference is visibility and not existence). The
        # consequence for THIS method is that two callers can resolve one title to DIFFERENT rows
        # at the same moment on the same board — measured — and that where nothing resolvable is
        # visible it mints a row beside one that already exists, which follows from the two lines
        # below rather than from a probe. Pinned by
        # test_a_label_on_no_readable_task_is_INVISIBLE_to_another_caller, in
        # tests/integration/test_duplicate_label.py.
        # Without the insensitive match an agent typing "Bug"/"bug " forks a second, colorless
        # label beside the canonical one (real incident 2026-07-08: a bot did exactly that).
        # `label_key` (module level) is the SINGLE statement of this resolution rule — the same
        # one `Workflow._has_label`/`_remove_label` read with, since #1256. Inlining it here
        # again is the "second spelling" that made those gates disagree with this method.
        want = label_key(title)
        for label in self.labels():
            if label_key(label.get("title")) == want:
                return label
        return self.create_label(title)
