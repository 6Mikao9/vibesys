"""Plugin registrations for the single-agent search presets: the core path.

Every entrypoint resolves `single-agent` and `profile-guided-single-agent` to these
registrations. The run is a vs-core flow driven by the vs-runtime shell; `single_core` composes it.
"""

from vibesys.orchestration.single.launch import ProfileGuidedSingleLaunch, SingleLaunch
from vibesys.single_core import single_core_registration

REGISTRATION = single_core_registration("single-agent", SingleLaunch)
PLUGIN = REGISTRATION.plugin
PROFILE_GUIDED_REGISTRATION = single_core_registration(
    "profile-guided-single-agent", ProfileGuidedSingleLaunch
)
PROFILE_GUIDED_PLUGIN = PROFILE_GUIDED_REGISTRATION.plugin

__all__ = ["PLUGIN", "PROFILE_GUIDED_PLUGIN", "PROFILE_GUIDED_REGISTRATION", "REGISTRATION"]
