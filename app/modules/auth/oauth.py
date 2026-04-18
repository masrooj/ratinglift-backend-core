from dataclasses import dataclass
from enum import Enum

import httpx
from fastapi import HTTPException


class SocialProvider(str, Enum):
    GOOGLE = "google"
    MICROSOFT = "microsoft"
    FACEBOOK = "facebook"


@dataclass
class SocialProfile:
    email: str
    name: str | None
    picture: str | None
    subject: str | None


async def _request_user_info(url: str, token: str, params: dict | None = None) -> dict:
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(url, headers=headers, params=params)

    if response.status_code >= 400:
        raise HTTPException(status_code=401, detail="Invalid OAuth token")

    return response.json()


async def verify_google_token(oauth_token: str) -> SocialProfile:
    data = await _request_user_info("https://www.googleapis.com/oauth2/v3/userinfo", oauth_token)

    email = data.get("email")
    if not email:
        raise HTTPException(status_code=400, detail="Google profile does not contain email")

    return SocialProfile(
        email=email.lower(),
        name=data.get("name"),
        picture=data.get("picture"),
        subject=data.get("sub"),
    )


async def verify_microsoft_token(oauth_token: str) -> SocialProfile:
    data = await _request_user_info("https://graph.microsoft.com/v1.0/me", oauth_token)

    email = data.get("mail") or data.get("userPrincipalName")
    if not email:
        raise HTTPException(status_code=400, detail="Microsoft profile does not contain email")

    return SocialProfile(
        email=email.lower(),
        name=data.get("displayName"),
        picture=None,
        subject=data.get("id"),
    )


async def verify_facebook_token(oauth_token: str) -> SocialProfile:
    data = await _request_user_info(
        "https://graph.facebook.com/me",
        oauth_token,
        params={"fields": "id,name,email,picture.type(large)"},
    )

    email = data.get("email")
    if not email:
        raise HTTPException(status_code=400, detail="Facebook profile does not contain email")

    picture = None
    picture_obj = data.get("picture")
    if isinstance(picture_obj, dict):
        picture_data = picture_obj.get("data")
        if isinstance(picture_data, dict):
            picture = picture_data.get("url")

    return SocialProfile(
        email=email.lower(),
        name=data.get("name"),
        picture=picture,
        subject=data.get("id"),
    )


async def verify_oauth_token(provider: SocialProvider, oauth_token: str) -> SocialProfile:
    if provider == SocialProvider.GOOGLE:
        return await verify_google_token(oauth_token)
    if provider == SocialProvider.MICROSOFT:
        return await verify_microsoft_token(oauth_token)
    if provider == SocialProvider.FACEBOOK:
        return await verify_facebook_token(oauth_token)

    raise HTTPException(status_code=400, detail="Unsupported social provider")
