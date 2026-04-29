"""
────────────────────────────────────────────────────────────────────────
─  Y U K I  C H A T I N G  —  E M A I L  T E M P L A T E S
─  Import: from YUKICHATING.Gmailtemplate.Template import render_otp_email, ...
────────────────────────────────────────────────────────────────────────
"""

_BASE = """
<html>
<body style="margin:0;padding:0;background:#0a0a0a;font-family:'Segoe UI',Arial,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0">
  <tr><td align="center" style="padding:40px 20px;">
    <table width="500" cellpadding="0" cellspacing="0"
      style="background:#111;border-radius:16px;border:1px solid #1e1e1e;
             box-shadow:0 0 40px rgba(99,102,241,0.15);overflow:hidden;">
      <!-- Header -->
      <tr>
        <td style="background:linear-gradient(135deg,#6366f1,#8b5cf6);
                   padding:28px 32px;text-align:center;">
          <h1 style="margin:0;color:#fff;font-size:22px;letter-spacing:2px;
                     font-weight:700;">{header_title}</h1>
          <p style="margin:6px 0 0;color:rgba(255,255,255,0.75);font-size:13px;">
            {header_sub}
          </p>
        </td>
      </tr>
      <!-- Body -->
      <tr>
        <td style="padding:32px;">
          {body}
        </td>
      </tr>
      <!-- Footer -->
      <tr>
        <td style="border-top:1px solid #1e1e1e;padding:20px 32px;text-align:center;">
          <p style="margin:0;color:#555;font-size:12px;">
            © 2025 YUKI CHATING — Do not reply to this email.
          </p>
        </td>
      </tr>
    </table>
  </td></tr>
</table>
</body>
</html>
"""

def _render(header_title: str, header_sub: str, body: str) -> str:
    return _BASE.format(
        header_title=header_title,
        header_sub=header_sub,
        body=body,
    )

def _text(content: str) -> str:
    return f'<p style="color:#ccc;font-size:15px;line-height:1.7;margin:0 0 16px;">{content}</p>'

def _otp_box(otp: int) -> str:
    return f"""
    <div style="text-align:center;margin:28px 0;">
      <span style="display:inline-block;background:#1a1a2e;border:1px solid #6366f1;
                   color:#a5b4fc;font-size:34px;font-weight:700;letter-spacing:10px;
                   padding:14px 32px;border-radius:10px;">
        {otp}
      </span>
      <p style="color:#555;font-size:12px;margin-top:10px;">
        ⏳ Valid for 10 minutes only
      </p>
    </div>
    """

def _button(text: str, url: str) -> str:
    return f"""
    <div style="text-align:center;margin:24px 0;">
      <a href="{url}" style="background:linear-gradient(135deg,#6366f1,#8b5cf6);
         color:#fff;text-decoration:none;padding:13px 32px;border-radius:8px;
         font-weight:600;font-size:15px;display:inline-block;">
        {text}
      </a>
    </div>
    """

def _alert_row(icon: str, label: str, value: str) -> str:
    return f"""
    <tr>
      <td style="padding:10px 16px;border-bottom:1px solid #1e1e1e;">
        <span style="color:#6366f1;">{icon}</span>
        <span style="color:#888;font-size:13px;margin-left:6px;">{label}</span>
      </td>
      <td style="padding:10px 16px;border-bottom:1px solid #1e1e1e;">
        <span style="color:#e5e7eb;font-size:13px;font-weight:600;">{value}</span>
      </td>
    </tr>
    """

# ══════════════════════════════════════════════════════════════════════════════

def render_otp_email(username: str, otp: int) -> str:
    body = (
        _text(f"Hello <b style='color:#a5b4fc;'>{username}</b>,")
        + _text("Thank you for signing up! Use the OTP below to verify your account:")
        + _otp_box(otp)
        + _text("If you did not request this, simply ignore this email.")
    )
    return _render(
        header_title="⚡ YUKI CHATING",
        header_sub="Account Verification",
        body=body,
    )


def render_reset_otp_email(username: str, otp: int) -> str:
    body = (
        _text(f"Hello <b style='color:#a5b4fc;'>{username}</b>,")
        + _text("We received a password reset request. Use the OTP below:")
        + _otp_box(otp)
        + _text(
            "<span style='color:#f87171;'>⚠️ If you didn't request this, "
            "change your password immediately!</span>"
        )
    )
    return _render(
        header_title="🔒 Password Reset",
        header_sub="YUKI CHATING Security",
        body=body,
    )


def render_welcome_email(username: str) -> str:
    body = (
        _text(f"Welcome to YUKI CHATING, <b style='color:#a5b4fc;'>{username}</b>! 🎉")
        + _text(
            "Your account has been successfully verified. "
            "You can now start chatting, create rooms, and connect with people."
        )
        + _button("Open YUKI CHATING", "https://yukichating.vercel.app/dashboard")
        + _text(
            "<span style='color:#555;font-size:13px;'>"
            "If the button doesn't work, visit: yukichating.vercel.app"
            "</span>"
        )
    )
    return _render(
        header_title="🎉 Welcome Aboard!",
        header_sub="Your account is ready",
        body=body,
    )


def render_login_alert(
    username: str,
    ip: str,
    location: str,
    time_str: str,
) -> str:
    table = f"""
    <table width="100%" cellpadding="0" cellspacing="0"
      style="background:#0d0d1a;border:1px solid #1e1e1e;
             border-radius:10px;margin:20px 0;overflow:hidden;">
      {_alert_row("🕐", "Time",     time_str)}
      {_alert_row("🌐", "IP Address", ip)}
      {_alert_row("📍", "Location", location)}
    </table>
    """
    body = (
        _text(f"Hello <b style='color:#a5b4fc;'>{username}</b>,")
        + _text("A new login was detected on your account:")
        + table
        + _text(
            "<span style='color:#f87171;'>⚠️ If this wasn't you, "
            "please reset your password immediately!</span>"
        )
        + _button("Reset Password", "https://yukichating.vercel.app/reset")
    )
    return _render(
        header_title="🚨 New Login Detected",
        header_sub="YUKI CHATING Security Alert",
        body=body,
    )
    
