"""OAuth popup HTML page generators used by multiple auth flows."""

import json
from html import escape


def generate_oauth_popup_success_page(service: str) -> str:
    """Generate an HTML page that notifies the opener window and closes the popup."""
    return f"""
    <!DOCTYPE html>
    <html>
    <head><title>Connected</title></head>
    <body>
        <p>Successfully connected {service}. This window will close automatically.</p>
        <script>
            if (window.opener) {{
                window.opener.postMessage({{
                    type: 'oauth_callback_success',
                    service: '{service}'
                }}, window.location.origin);
                window.close();
            }} else {{
                // Fallback: if no opener (popup blocked, opened as new tab), redirect to app
                window.location.href = '/';
            }}
        </script>
    </body>
    </html>
    """


def generate_oauth_popup_error_page(service: str, error_message: str) -> str:
    """Generate an HTML page that notifies the opener of failure and offers to close.

    ``error_message`` may echo upstream provider text, so it is escaped for the
    HTML body and JSON-encoded (then attribute-escaped) for the inline handler.
    """
    service_html = escape(service)
    message_html = escape(error_message)
    service_js = escape(json.dumps(service))
    message_js = escape(json.dumps(error_message))
    return f"""
    <!DOCTYPE html>
    <html>
    <head><title>Connection Failed</title></head>
    <body style="font-family: sans-serif; max-width: 600px; margin: 50px auto; padding: 20px;">
        <h1>{service_html} Connection Failed</h1>
        <p style="color: red;">{message_html}</p>
        <p><button onclick="
            if (window.opener) {{
                window.opener.postMessage({{
                    type: 'oauth_callback_error',
                    service: {service_js},
                    error: {message_js}
                }}, window.location.origin);
            }}
            window.close();
        ">Close this window</button></p>
        <p><a href="/">Return to app</a></p>
    </body>
    </html>
    """
