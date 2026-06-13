from pydantic import BaseModel


class LegalMetadataOut(BaseModel):
    terms_version: str
    privacy_version: str
    effective_date: str
    effective_date_label: str
    terms_url: str
    privacy_url: str
    legal_contact_email: str
    support_contact_email: str
    operator_name: str
