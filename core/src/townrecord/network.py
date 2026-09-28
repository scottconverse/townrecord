"""The address the API answers on, and who can reach it (spec 13.1).

Spec 13.1 puts the service on 127.0.0.1 by default and makes serving on a local
network an option the user turns on, with a warning. Two things follow, and both
belong here rather than in the command, because the report and the command must
agree about them:

* an address that is not this machine alone is refused until the option is on,
  and the refusal is a plain sentence naming the setting to change;
* once it is on, the warning says who can reach the API there and that a token is
  still required, so nobody reads "it is on my network" as "it is open".

An empty host is not loopback: binding an empty host name is how the operating
system is asked for *every* interface, so the safe direction is to treat it as
somewhere the network can reach. A name that is not an IP address, such as a
machine name, is treated the same way: this module never guesses which names
resolve to this machine.

Nothing here opens a socket. The refusal is decided from the settings alone, so
a service that was never allowed off the loopback interface never binds one.
"""

from __future__ import annotations

import ipaddress

from .config import DEFAULT_HOST

#: The name that means this machine in every ordinary configuration.
LOOPBACK_NAME = "localhost"

#: The setting that turns network serving on (spec 13.1).
ALLOW_LAN_VAR = "TOWNRECORD_ALLOW_LAN"


def is_loopback(host: str) -> bool:
    """Say whether this address is this machine only.

    The empty host, and anything that is not an IP address or ``localhost``, is
    not loopback: this is a question about what the socket will accept, and the
    answer for a name is something only the resolver knows.
    """
    text = host.strip().strip("[]").strip()
    if not text:
        return False
    if text.lower() == LOOPBACK_NAME:
        return True
    try:
        return ipaddress.ip_address(text).is_loopback
    except ValueError:
        return False


def lan_refusal(host: str, *, allow_lan: bool) -> str | None:
    """The sentence ``serve`` refuses this address with, or None when it may serve.

    It is the sentence and not an exception, because both the command and the
    report print it: a shell whose configuration would be refused says so in its
    status rather than warning about an exposure that cannot happen yet.
    """
    if is_loopback(host) or allow_lan:
        return None
    return (
        f"TownRecord serves on {DEFAULT_HOST} unless serving on the local network is "
        f"turned on, and {host} is not this machine, so it was not started. Anyone who "
        f"can reach {host} would reach the API. To serve on the local network, set "
        f"{ALLOW_LAN_VAR}=1."
    )


def lan_warning(host: str, port: int, *, allow_lan: bool) -> str | None:
    """The warning this address carries, or None when only this machine is reached.

    It names who can reach the API and says the token is still required, because
    turning the option on is a change in who can ask, not in what they must show
    (spec 13.1, decision 10).
    """
    if is_loopback(host) or not allow_lan:
        return None
    return (
        f"Warning: the API answers on http://{host}:{port}, which is not this machine "
        f"alone, so any computer that can reach this one on the local network can reach "
        f"it. Every request still needs a token."
    )


__all__ = [
    "ALLOW_LAN_VAR",
    "LOOPBACK_NAME",
    "is_loopback",
    "lan_refusal",
    "lan_warning",
]
