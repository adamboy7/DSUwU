try:
    from libraries.inputs import register_input_scripts_from_env
except Exception:
    register_input_scripts_from_env = None

if register_input_scripts_from_env is not None:
    register_input_scripts_from_env()
