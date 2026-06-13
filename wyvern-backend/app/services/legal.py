from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from functools import lru_cache
from html import escape
from pathlib import Path
import re


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
LEGAL_DIR = PROJECT_ROOT / "legal"

LEGAL_CONTACT_EMAIL = "legal@wyvernhub.net"
LEGAL_SUPPORT_EMAIL = "support@wyvernhub.net"
LEGAL_OPERATOR_NAME = "Wyvern Team"
LEGAL_EFFECTIVE_DATE = date(2026, 5, 22)
LEGAL_EFFECTIVE_DATE_LABEL = "May 22, 2026"
TERMS_VERSION = "2026-05-22"
PRIVACY_VERSION = "2026-05-22"
LEGAL_RECONSENT_ERROR_CODE = "LEGAL_RECONSENT_REQUIRED"
LEGAL_RECONSENT_MESSAGE = "You must accept the latest Terms of Service and Privacy Policy before continuing."


@dataclass(frozen=True)
class LegalDocument:
    slug: str
    title: str
    version: str
    filename: str


TERMS_DOCUMENT = LegalDocument(
    slug="terms",
    title="Terms of Service",
    version=TERMS_VERSION,
    filename="terms.md",
)
PRIVACY_DOCUMENT = LegalDocument(
    slug="privacy",
    title="Privacy Policy",
    version=PRIVACY_VERSION,
    filename="privacy.md",
)

LEGAL_DOCUMENTS = {
    TERMS_DOCUMENT.slug: TERMS_DOCUMENT,
    PRIVACY_DOCUMENT.slug: PRIVACY_DOCUMENT,
}


def legal_terms_path(base_url: str) -> str:
    return f"{base_url.rstrip('/')}/legal/{TERMS_DOCUMENT.slug}"


def legal_privacy_path(base_url: str) -> str:
    return f"{base_url.rstrip('/')}/legal/{PRIVACY_DOCUMENT.slug}"


def current_legal_versions() -> dict[str, str]:
    return {
        "terms_version": TERMS_DOCUMENT.version,
        "privacy_version": PRIVACY_DOCUMENT.version,
    }


def legal_metadata(base_url: str) -> dict[str, str]:
    normalized_base = base_url.rstrip("/")
    return {
        "terms_version": TERMS_DOCUMENT.version,
        "privacy_version": PRIVACY_DOCUMENT.version,
        "effective_date": LEGAL_EFFECTIVE_DATE.isoformat(),
        "effective_date_label": LEGAL_EFFECTIVE_DATE_LABEL,
        "terms_url": legal_terms_path(normalized_base),
        "privacy_url": legal_privacy_path(normalized_base),
        "legal_contact_email": LEGAL_CONTACT_EMAIL,
        "support_contact_email": LEGAL_SUPPORT_EMAIL,
        "operator_name": LEGAL_OPERATOR_NAME,
    }


def user_requires_legal_reacceptance(user: object) -> bool:
    accepted_terms = str(getattr(user, "accepted_terms_version", "") or "").strip()
    accepted_privacy = str(getattr(user, "accepted_privacy_version", "") or "").strip()
    return accepted_terms != TERMS_DOCUMENT.version or accepted_privacy != PRIVACY_DOCUMENT.version


def apply_current_legal_acceptance(user: object, *, accepted_at: datetime | None = None) -> None:
    now = accepted_at or datetime.now(tz=UTC)
    setattr(user, "accepted_terms_version", TERMS_DOCUMENT.version)
    setattr(user, "accepted_privacy_version", PRIVACY_DOCUMENT.version)
    setattr(user, "legal_accepted_at", now)


def validate_legal_acceptance_payload(
    *,
    accepted_legal: bool,
    terms_version: str,
    privacy_version: str,
) -> None:
    if not accepted_legal:
        raise ValueError("You must accept the Terms of Service and Privacy Policy")
    if str(terms_version).strip() != TERMS_DOCUMENT.version:
        raise ValueError("You must accept the current Terms of Service version")
    if str(privacy_version).strip() != PRIVACY_DOCUMENT.version:
        raise ValueError("You must accept the current Privacy Policy version")


def load_legal_document(slug: str) -> LegalDocument:
    document = LEGAL_DOCUMENTS.get(slug)
    if document is None:
        raise FileNotFoundError(f"Unknown legal document: {slug}")
    return document


@lru_cache(maxsize=8)
def read_legal_markdown(slug: str) -> str:
    document = load_legal_document(slug)
    return (LEGAL_DIR / document.filename).read_text(encoding="utf-8")


def _inline_markdown(text: str) -> str:
    escaped = escape(text)
    escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
    escaped = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"\*([^*]+)\*", r"<em>\1</em>", escaped)
    escaped = re.sub(
        r"\[([^\]]+)\]\(([^)]+)\)",
        lambda match: f'<a href="{escape(match.group(2), quote=True)}">{match.group(1)}</a>',
        escaped,
    )
    return escaped


def render_markdown_html(markdown_text: str) -> str:
    lines = markdown_text.splitlines()
    html: list[str] = []
    paragraph: list[str] = []
    current_list_type: str | None = None

    def flush_paragraph() -> None:
        nonlocal paragraph
        if not paragraph:
            return
        html.append(f"<p>{_inline_markdown(' '.join(item.strip() for item in paragraph))}</p>")
        paragraph = []

    def close_list() -> None:
        nonlocal current_list_type
        if current_list_type is not None:
            html.append(f"</{current_list_type}>")
            current_list_type = None

    for raw_line in lines:
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped:
            flush_paragraph()
            close_list()
            continue

        heading_match = re.match(r"^(#{1,3})\s+(.*)$", stripped)
        if heading_match:
            flush_paragraph()
            close_list()
            level = len(heading_match.group(1))
            html.append(f"<h{level}>{_inline_markdown(heading_match.group(2).strip())}</h{level}>")
            continue

        unordered_match = re.match(r"^[-*]\s+(.*)$", stripped)
        if unordered_match:
            flush_paragraph()
            if current_list_type != "ul":
                close_list()
                current_list_type = "ul"
                html.append("<ul>")
            html.append(f"<li>{_inline_markdown(unordered_match.group(1).strip())}</li>")
            continue

        ordered_match = re.match(r"^\d+\.\s+(.*)$", stripped)
        if ordered_match:
            flush_paragraph()
            if current_list_type != "ol":
                close_list()
                current_list_type = "ol"
                html.append("<ol>")
            html.append(f"<li>{_inline_markdown(ordered_match.group(1).strip())}</li>")
            continue

        paragraph.append(stripped)

    flush_paragraph()
    close_list()
    return "\n".join(html)


def render_legal_page_html(document: LegalDocument, *, base_url: str) -> str:
    body_html = render_markdown_html(read_legal_markdown(document.slug))
    terms_url = legal_terms_path(base_url)
    privacy_url = legal_privacy_path(base_url)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Wyvern · {escape(document.title)}</title>
  <link rel="icon" href="/static/wyvern-logo.png" />
  <style>
    :root {{
      --brand: #00c8ff;
      --bg: #030303;
      --surface: #0e0e0e;
      --line: rgba(255, 255, 255, 0.08);
      --text: #ffffff;
      --muted: rgba(255, 255, 255, 0.66);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      min-height: 100vh;
      background: radial-gradient(circle at top, rgba(0, 200, 255, 0.1), transparent 24%), var(--bg);
      color: var(--text);
      font-family: Inter, system-ui, sans-serif;
      line-height: 1.7;
    }}
    main {{
      max-width: 920px;
      margin: 0 auto;
      padding: 48px 20px 96px;
    }}
    .shell {{
      background: rgba(8, 8, 8, 0.96);
      border: 1px solid var(--line);
      border-radius: 18px;
      overflow: hidden;
      box-shadow: 0 24px 80px rgba(0, 0, 0, 0.45);
    }}
    .hero {{
      padding: 28px 28px 22px;
      border-bottom: 1px solid var(--line);
    }}
    .eyebrow {{
      color: var(--brand);
      font-size: 12px;
      letter-spacing: 2px;
      text-transform: uppercase;
    }}
    h1 {{
      margin: 10px 0 8px;
      font-size: 34px;
      line-height: 1.1;
    }}
    .meta {{
      color: var(--muted);
      font-size: 14px;
    }}
    nav {{
      display: flex;
      flex-wrap: wrap;
      gap: 12px;
      margin-top: 16px;
    }}
    nav a {{
      color: var(--brand);
      text-decoration: none;
    }}
    article {{
      padding: 28px;
    }}
    article h2, article h3 {{
      margin-top: 28px;
      margin-bottom: 10px;
      line-height: 1.2;
    }}
    article p {{
      margin: 14px 0;
      color: rgba(255, 255, 255, 0.84);
    }}
    article ul, article ol {{
      margin: 14px 0 14px 22px;
      color: rgba(255, 255, 255, 0.84);
    }}
    article a {{
      color: var(--brand);
    }}
    article code {{
      background: rgba(255, 255, 255, 0.08);
      padding: 0.1rem 0.35rem;
      border-radius: 6px;
    }}
  </style>
</head>
<body>
  <main>
    <div class="shell">
      <section class="hero">
        <div class="eyebrow">Wyvern Legal</div>
        <h1>{escape(document.title)}</h1>
        <div class="meta">Version {escape(document.version)} · Effective {escape(LEGAL_EFFECTIVE_DATE_LABEL)}</div>
        <nav>
          <a href="{escape(terms_url, quote=True)}">Terms of Service</a>
          <a href="{escape(privacy_url, quote=True)}">Privacy Policy</a>
          <a href="mailto:{escape(LEGAL_CONTACT_EMAIL, quote=True)}">Legal Contact</a>
          <a href="mailto:{escape(LEGAL_SUPPORT_EMAIL, quote=True)}">Support</a>
        </nav>
      </section>
      <article>{body_html}</article>
    </div>
  </main>
</body>
</html>
"""
