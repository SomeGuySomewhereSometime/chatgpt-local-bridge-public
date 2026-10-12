"""One registration path; excluded tools are never registered or dispatchable."""
import os

DESKTOP = frozenset('screen_capture app_launch app_close process_kill desktop_status desktop_observe desktop_window desktop_query desktop_session desktop_browser_act desktop_act obs_status obs_start_recording obs_stop_recording obs_extract_frames'.split())
SHARED = frozenset('system_info get_session_context operation_request resource_lease integration_status process_list process_info journal_query'.split())
PROFILE = os.environ.get('BRIDGE_PROFILE', 'core')
if PROFILE not in {'core', 'desktop'}:
    raise ValueError('BRIDGE_PROFILE must be core or desktop')


def allowed(name, profile=PROFILE):
    return name not in DESKTOP if profile == 'core' else name in DESKTOP | SHARED


def registrar(server):
    def tool(*args, **kwargs):
        def register(fn):
            if allowed(fn.__name__):
                server.tool(*args, **kwargs)(fn)
            return fn
        return register
    return tool
