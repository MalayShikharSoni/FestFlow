"""Server-side QR codes for tickets, embedded straight into the page."""
import base64
from io import BytesIO

import qrcode


def qr_data_uri(text: str) -> str:
    """PNG QR code as a data: URI — generated in memory, never written to disk.

    The QR holds only the ticket code; the gate's check-in screen looks it up.
    """
    image = qrcode.make(text, box_size=6, border=2)
    buffer = BytesIO()
    image.save(buffer)
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")
