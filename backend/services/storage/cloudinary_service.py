from datetime import datetime, timezone
import logging
import os
from pathlib import Path
import re
from typing import Any, Dict, Optional
import uuid

from backend.core.config import BASE_DIR, settings

logger = logging.getLogger(__name__)


class CloudinaryService:
    """
    Production Cloud Storage Service for Cortex.
    
    Folder Hierarchy (Root: 'cortex/'):
      - 'cortex/documents/': Enterprise knowledge documents (restricted/authenticated).
      - 'cortex/jira_tickets/': User-uploaded attachments/receipts/screenshots for Jira tickets.
    
    Asset Naming Convention for Jira Ticket Images:
      <username>-<YYYYMMDD-HHMMSS>.<ext>
      e.g. employee_eng-20261119-104523.png
      The username precedes the dash '-' so all assets can easily be filtered and attributed by user.
    
    Security Guardrail:
      This service is strictly internal backend infrastructure.
      It does NOT expose any functions or tools to the AI agent or LLM planner.
      The AI agent remains completely unaware of Cloudinary.
    """

    def __init__(self) -> None:
        self.root_folder = settings.CLOUDINARY_ROOT_FOLDER or "cortex"
        self._is_configured: Optional[bool] = None
        self._init_client()

    def _init_client(self) -> None:
        has_url = bool(settings.CLOUDINARY_URL and settings.CLOUDINARY_URL.strip())
        has_credentials = bool(
            settings.CLOUDINARY_CLOUD_NAME
            and settings.CLOUDINARY_API_KEY
            and settings.CLOUDINARY_API_SECRET
        )
        self._is_configured = has_url or has_credentials

        if self._is_configured:
            try:
                import cloudinary
                if has_url:
                    cloudinary.config(cloudinary_url=settings.CLOUDINARY_URL)
                else:
                    cloudinary.config(
                        cloud_name=settings.CLOUDINARY_CLOUD_NAME.strip(),
                        api_key=settings.CLOUDINARY_API_KEY.strip(),
                        api_secret=settings.CLOUDINARY_API_SECRET.strip(),
                        secure=True,
                    )
                logger.info("Cloudinary storage service initialized successfully (root: %s)", self.root_folder)
            except Exception as e:
                logger.warning("Failed to configure Cloudinary SDK (%s). Falling back to local storage.", e)
                self._is_configured = False
        else:
            logger.info("Cloudinary credentials not detected; using resilient local storage fallback.")

    @property
    def is_configured(self) -> bool:
        if self._is_configured is None:
            self._init_client()
        return bool(self._is_configured)

    def format_jira_image_filename(self, username: str, original_filename: str) -> str:
        """
        Formats standardized Jira ticket attachment filename:
          <username>-<YYYYMMDD-HHMMSS>.<ext>
        The username is sanitized to lowercase alphanumeric + underscores.
        """
        clean_user = re.sub(r"[^a-zA-Z0-9_]", "_", username.lower().strip()) or "user"
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        ext = Path(original_filename).suffix.lower() or ".png"
        return f"{clean_user}-{timestamp}{ext}"

    def upload_jira_attachment(
        self,
        file_bytes: bytes,
        username: str,
        original_filename: str,
    ) -> Dict[str, Any]:
        """
        Uploads a Jira ticket attachment (image/receipt) under:
          cortex/jira_tickets/<username>-<YYYYMMDD-HHMMSS>.<ext>
        """
        formatted_name = self.format_jira_image_filename(username, original_filename)
        folder = f"{self.root_folder}/jira_tickets"
        public_id_bare = Path(formatted_name).stem
        public_id = f"{folder}/{public_id_bare}"

        if self.is_configured:
            try:
                import cloudinary.uploader
                upload_res = cloudinary.uploader.upload(
                    file_bytes,
                    public_id=public_id,
                    folder=folder,
                    resource_type="auto",
                    overwrite=True,
                )
                logger.info("Uploaded Jira image to Cloudinary: %s", upload_res.get("secure_url"))
                return {
                    "url": upload_res.get("secure_url"),
                    "public_id": upload_res.get("public_id"),
                    "filename": formatted_name,
                    "size_bytes": len(file_bytes),
                    "storage_type": "cloudinary",
                }
            except Exception as e:
                logger.warning("Cloudinary upload failed (%s). Falling back to local disk.", e)

        # Resilient Local Storage Fallback
        local_dir = BASE_DIR / "storage" / self.root_folder / "jira_tickets"
        local_dir.mkdir(parents=True, exist_ok=True)
        dest = local_dir / formatted_name
        dest.write_bytes(file_bytes)
        local_url = f"/api/v1/storage/jira_tickets/{formatted_name}"
        logger.info("Saved Jira image locally: %s", dest)

        return {
            "url": local_url,
            "public_id": f"local:{formatted_name}",
            "filename": formatted_name,
            "storage_path": str(dest),
            "size_bytes": len(file_bytes),
            "storage_type": "local",
        }

    def upload_document(
        self,
        file_bytes: bytes,
        document_id: uuid.UUID,
        original_filename: str,
    ) -> Dict[str, Any]:
        """
        Uploads an enterprise source document under:
          cortex/documents/doc_<document_id>.<ext>
        """
        ext = Path(original_filename).suffix.lower() or ".bin"
        filename = f"{document_id}{ext}"
        folder = f"{self.root_folder}/documents"
        public_id_bare = f"doc_{document_id}"
        public_id = f"{folder}/{public_id_bare}"

        if self.is_configured:
            try:
                import cloudinary.uploader
                upload_res = cloudinary.uploader.upload(
                    file_bytes,
                    public_id=public_id,
                    folder=folder,
                    resource_type="raw",
                    overwrite=True,
                )
                logger.info("Uploaded enterprise document to Cloudinary: %s", upload_res.get("secure_url"))
                return {
                    "url": upload_res.get("secure_url"),
                    "public_id": upload_res.get("public_id"),
                    "filename": filename,
                    "size_bytes": len(file_bytes),
                    "storage_type": "cloudinary",
                }
            except Exception as e:
                logger.warning("Cloudinary document upload failed (%s). Falling back to local disk.", e)

        # Resilient Local Storage Fallback
        local_dir = BASE_DIR / "storage" / self.root_folder / "documents"
        local_dir.mkdir(parents=True, exist_ok=True)
        dest = local_dir / filename
        dest.write_bytes(file_bytes)
        logger.info("Saved enterprise document locally: %s", dest)

        return {
            "url": str(dest),
            "public_id": f"local:{filename}",
            "filename": filename,
            "storage_path": str(dest),
            "size_bytes": len(file_bytes),
            "storage_type": "local",
        }


# Global singleton instance
cloudinary_service = CloudinaryService()
