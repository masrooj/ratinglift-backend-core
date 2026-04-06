from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/auth", tags=["auth"])

class LoginRequest(BaseModel):
    username: str
    password: str

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"

@router.post("/login", response_model=TokenResponse)
async def login(login_data: LoginRequest):
    """Authenticate user and return access token"""
    # This is a placeholder - in real implementation, verify credentials
    if login_data.username == "admin" and login_data.password == "password":
        return {"access_token": "fake-jwt-token", "token_type": "bearer"}
    raise HTTPException(status_code=401, detail="Invalid credentials")

@router.post("/logout")
async def logout():
    """Logout user (invalidate token)"""
    return {"message": "Successfully logged out"}

@router.get("/me")
async def get_current_user():
    """Get current authenticated user info"""
    return {"username": "current_user", "email": "user@example.com", "role": "admin"}