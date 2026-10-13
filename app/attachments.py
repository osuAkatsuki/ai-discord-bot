from pathlib import PurePosixPath
from urllib.parse import urlsplit

import discord

from app import ai

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
FILE_EXTENSIONS = {
    ".pdf",
    ".txt",
    ".md",
    ".csv",
    ".tsv",
    ".docx",
    ".pptx",
    ".xlsx",
    ".json",
    ".py",
    ".js",
    ".ts",
    ".html",
    ".css",
}
MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024
MAX_REQUEST_FILE_BYTES = 50 * 1024 * 1024


def extension(filename: str) -> str:
    return PurePosixPath(urlsplit(filename).path).suffix.lower()


def content_from_attachments(
    attachments: list[discord.Attachment],
) -> list[ai.MessageContent]:
    if sum(a.size for a in attachments) >= MAX_REQUEST_FILE_BYTES:
        raise ValueError("Attachments must total less than 50 MiB per message.")
    content: list[ai.MessageContent] = []
    for attachment in attachments:
        if attachment.size > MAX_ATTACHMENT_BYTES:
            raise ValueError(
                f"{attachment.filename} exceeds the bot's 20 MiB attachment limit."
            )
        suffix = extension(attachment.filename)
        if suffix in IMAGE_EXTENSIONS:
            content.append({"type": "image_url", "image_url": {"url": attachment.url}})
        elif suffix in FILE_EXTENSIONS:
            content.append({"type": "file", "file_url": attachment.url})
        else:
            raise ValueError(
                f"Unsupported attachment: {attachment.filename}. Use an image, PDF, document, spreadsheet, or text file."
            )
    return content
