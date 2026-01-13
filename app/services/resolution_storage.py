"""
Resolution Storage Service.

Manages storage of conflict resolution packages in S3/MinIO.
Handles upload, download, and URL generation for resolution artifacts.

Per Workstream 3 specification.
"""

import json
import logging
import boto3
from botocore.exceptions import ClientError
from botocore.client import Config
from typing import Dict, Optional, Any
from datetime import datetime, timedelta

from app.config import get_config

logger = logging.getLogger(__name__)


class ResolutionPackage:
    """
    Resolution package data structure.

    Attributes:
        resolution_id: Unique ID for the resolution
        resolved_files: Dict mapping file paths to resolved content
        metadata: Additional metadata about the resolution
    """

    def __init__(
        self,
        resolution_id: str,
        resolved_files: Dict[str, str],
        metadata: Optional[Dict[str, Any]] = None
    ):
        self.resolution_id = resolution_id
        self.resolved_files = resolved_files
        self.metadata = metadata or {}

    def to_dict(self) -> Dict:
        """Convert to dictionary for serialization."""
        return {
            "resolution_id": self.resolution_id,
            "resolved_files": self.resolved_files,
            "metadata": self.metadata
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "ResolutionPackage":
        """Create from dictionary."""
        return cls(
            resolution_id=data["resolution_id"],
            resolved_files=data["resolved_files"],
            metadata=data.get("metadata", {})
        )


class ResolutionStorageService:
    """
    Service for storing resolution packages in S3/MinIO.

    Handles:
    - Uploading resolution packages as JSON
    - Downloading resolution packages
    - Generating presigned URLs
    - Deleting expired resolutions

    Usage:
        service = ResolutionStorageService()
        url, key, size = await service.store_resolution(
            resolution_id="abc-123",
            resolved_files={"src/main.py": "resolved content"},
            metadata={"strategy": "ours", "confidence": 0.95}
        )
    """

    def __init__(self):
        """Initialize storage service with S3/MinIO configuration."""
        self.config = get_config()

        # Configure boto3 client for S3 or MinIO
        s3_config = Config(
            signature_version='s3v4',
            s3={'addressing_style': 'path'}  # Required for MinIO
        )

        self.s3_client = boto3.client(
            's3',
            endpoint_url=self.config.s3_endpoint,  # None for AWS S3
            aws_access_key_id=self.config.s3_access_key,
            aws_secret_access_key=self.config.s3_secret_key,
            region_name=self.config.s3_region,
            config=s3_config
        )

        self.bucket_name = self.config.s3_bucket

        logger.info(
            f"Resolution storage initialized",
            extra={
                "bucket": self.bucket_name,
                "endpoint": self.config.s3_endpoint or "AWS S3",
                "region": self.config.s3_region
            }
        )

        # Ensure bucket exists
        self._ensure_bucket_exists()

    def _ensure_bucket_exists(self):
        """
        Ensure S3 bucket exists, create if necessary.

        Note: This is best done during deployment, but included for development.
        """
        try:
            self.s3_client.head_bucket(Bucket=self.bucket_name)
            logger.info(f"Bucket '{self.bucket_name}' exists")

        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code')

            if error_code == '404':
                # Bucket doesn't exist, create it
                logger.warning(
                    f"Bucket '{self.bucket_name}' not found, creating..."
                )

                try:
                    if self.config.s3_region == 'us-east-1':
                        # us-east-1 doesn't require LocationConstraint
                        self.s3_client.create_bucket(Bucket=self.bucket_name)
                    else:
                        self.s3_client.create_bucket(
                            Bucket=self.bucket_name,
                            CreateBucketConfiguration={
                                'LocationConstraint': self.config.s3_region
                            }
                        )

                    logger.info(f"Bucket '{self.bucket_name}' created successfully")

                except Exception as create_error:
                    logger.error(
                        f"Failed to create bucket",
                        extra={
                            "bucket": self.bucket_name,
                            "error": str(create_error)
                        },
                        exc_info=True
                    )
                    # Don't fail initialization, just warn
                    logger.warning("S3 bucket may not be available!")

            else:
                logger.error(
                    f"Error checking bucket",
                    extra={
                        "bucket": self.bucket_name,
                        "error_code": error_code
                    }
                )

    async def store_resolution(
        self,
        resolution_id: str,
        resolved_files: Dict[str, str],
        metadata: Optional[Dict[str, Any]] = None
    ) -> tuple[str, str, int]:
        """
        Store resolution package in S3/MinIO.

        Args:
            resolution_id: Unique resolution ID (UUID)
            resolved_files: Dict mapping file paths to resolved content
            metadata: Additional metadata (strategy, confidence, etc.)

        Returns:
            Tuple of (url, key, size_bytes)

        Raises:
            RuntimeError: If upload fails
        """
        # Create resolution package
        package = ResolutionPackage(
            resolution_id=resolution_id,
            resolved_files=resolved_files,
            metadata=metadata or {}
        )

        # Serialize to JSON
        json_data = json.dumps(package.to_dict(), indent=2)
        json_bytes = json_data.encode('utf-8')
        size_bytes = len(json_bytes)

        # Generate S3 key
        key = f"resolutions/{resolution_id}.json"

        logger.info(
            f"Storing resolution package",
            extra={
                "resolution_id": resolution_id,
                "key": key,
                "size_bytes": size_bytes,
                "file_count": len(resolved_files)
            }
        )

        try:
            # Upload to S3
            self.s3_client.put_object(
                Bucket=self.bucket_name,
                Key=key,
                Body=json_bytes,
                ContentType='application/json',
                Metadata={
                    'resolution_id': resolution_id,
                    'stored_at': datetime.utcnow().isoformat()
                }
            )

            # Generate URL
            if self.config.s3_endpoint:
                # MinIO/custom endpoint
                url = f"{self.config.s3_endpoint}/{self.bucket_name}/{key}"
            else:
                # AWS S3
                url = f"https://{self.bucket_name}.s3.{self.config.s3_region}.amazonaws.com/{key}"

            logger.info(
                f"Resolution package stored successfully",
                extra={
                    "resolution_id": resolution_id,
                    "url": url,
                    "size_bytes": size_bytes
                }
            )

            return url, key, size_bytes

        except ClientError as e:
            logger.error(
                f"Failed to store resolution package",
                extra={
                    "resolution_id": resolution_id,
                    "error": str(e)
                },
                exc_info=True
            )
            raise RuntimeError(f"Failed to store resolution: {str(e)}")

    async def get_resolution(
        self,
        resolution_id: str
    ) -> Optional[ResolutionPackage]:
        """
        Retrieve resolution package from S3/MinIO.

        Args:
            resolution_id: Resolution ID

        Returns:
            ResolutionPackage or None if not found

        Raises:
            RuntimeError: If download fails (excluding not found)
        """
        key = f"resolutions/{resolution_id}.json"

        logger.info(
            f"Retrieving resolution package",
            extra={
                "resolution_id": resolution_id,
                "key": key
            }
        )

        try:
            response = self.s3_client.get_object(
                Bucket=self.bucket_name,
                Key=key
            )

            # Read and parse JSON
            json_data = response['Body'].read().decode('utf-8')
            package_dict = json.loads(json_data)

            package = ResolutionPackage.from_dict(package_dict)

            logger.info(
                f"Resolution package retrieved successfully",
                extra={
                    "resolution_id": resolution_id,
                    "file_count": len(package.resolved_files)
                }
            )

            return package

        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code')

            if error_code == 'NoSuchKey':
                logger.warning(
                    f"Resolution package not found",
                    extra={"resolution_id": resolution_id}
                )
                return None
            else:
                logger.error(
                    f"Failed to retrieve resolution package",
                    extra={
                        "resolution_id": resolution_id,
                        "error": str(e)
                    },
                    exc_info=True
                )
                raise RuntimeError(f"Failed to retrieve resolution: {str(e)}")

    async def generate_signed_url(
        self,
        resolution_id: str,
        expiration: int = 3600
    ) -> str:
        """
        Generate presigned URL for resolution package.

        Args:
            resolution_id: Resolution ID
            expiration: URL expiration time in seconds (default: 1 hour)

        Returns:
            str: Presigned URL

        Raises:
            RuntimeError: If URL generation fails
        """
        key = f"resolutions/{resolution_id}.json"

        try:
            url = self.s3_client.generate_presigned_url(
                'get_object',
                Params={
                    'Bucket': self.bucket_name,
                    'Key': key
                },
                ExpiresIn=expiration
            )

            logger.info(
                f"Generated presigned URL",
                extra={
                    "resolution_id": resolution_id,
                    "expiration": expiration
                }
            )

            return url

        except ClientError as e:
            logger.error(
                f"Failed to generate presigned URL",
                extra={
                    "resolution_id": resolution_id,
                    "error": str(e)
                },
                exc_info=True
            )
            raise RuntimeError(f"Failed to generate URL: {str(e)}")

    async def delete_resolution(
        self,
        resolution_id: str
    ) -> bool:
        """
        Delete resolution package from S3/MinIO.

        Args:
            resolution_id: Resolution ID

        Returns:
            bool: True if deleted, False if not found
        """
        key = f"resolutions/{resolution_id}.json"

        try:
            self.s3_client.delete_object(
                Bucket=self.bucket_name,
                Key=key
            )

            logger.info(
                f"Resolution package deleted",
                extra={"resolution_id": resolution_id}
            )

            return True

        except ClientError as e:
            logger.error(
                f"Failed to delete resolution package",
                extra={
                    "resolution_id": resolution_id,
                    "error": str(e)
                },
                exc_info=True
            )
            return False
