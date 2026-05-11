APP_NAME = "Bench Test Controller"
APP_VERSION = "14.3.0"


def window_title(suffix: str = "") -> str:
    title = f"{APP_NAME} v{APP_VERSION}"
    if suffix:
        return f"{title}  {suffix}"
    return title
