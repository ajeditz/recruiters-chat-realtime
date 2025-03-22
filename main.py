from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from typing import Dict, List, Optional
# from pydantic import BaseModel
from datetime import datetime
from pydantic import BaseModel, Field
import firebase_admin
from firebase_admin import credentials, firestore , initialize_app
import uuid
import json
from datetime import datetime, timezone
import os
from fastapi.staticfiles import StaticFiles

from dotenv import load_dotenv
load_dotenv()

# Initialize Firebase app with credentials
if not firebase_admin._apps:
    cred = credentials.Certificate(os.getenv("CRED_PATH"))  # Path to your Firebase service account key
    firebase_admin.initialize_app(cred)
db = firestore.client()

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class Message(BaseModel):
    id: Optional[str] = None
    sender_id: str
    recipient_id: str
    content: dict = {}
    timestamp: Optional[datetime] = Field(default_factory=datetime.now)
    read: Optional[bool] = False

class User(BaseModel):
    id: Optional[str]=None
    name: str
    online: bool = False
    last_seen: Optional[float] = None
    profile_pic_url: Optional[str] = None  # Add this field

    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }

class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, WebSocket] = {}
    
    async def connect(self, user_id: str, websocket: WebSocket):
        await websocket.accept()
        self.active_connections[user_id] = websocket
        
        # Update user's online status
        await update_user_status(user_id, True)
        
        # Fetch and send unread messages
        unread_messages = await get_unread_messages(user_id)
        for message in unread_messages:
            await websocket.send_json(message)
    
    def disconnect(self, user_id: str):
        if user_id in self.active_connections:
            del self.active_connections[user_id]
            # Update user status asynchronously (can't use await here directly)
            update_user_status_sync(user_id, False)
    
    async def send_personal_message(self, message: dict, recipient_id: str):
        # Store message in Firestore first
        msg_id = await store_message(message)
        
        # Add message ID to the object
        message["id"] = msg_id
        
        # If recipient is connected, send message directly
        if recipient_id in self.active_connections:
            await self.active_connections[recipient_id].send_json(message)
            # Mark as read if delivered
            await mark_message_as_read(msg_id)

manager = ConnectionManager()

# Helper functions for Firestore interaction
async def store_message(message: dict) -> str:
    """Store a message in Firestore and return the message ID"""
    if "id" not in message or not message["id"]:
        message["id"] = str(uuid.uuid4())
    
    # Make sure timestamp exists and convert to ISO format string
    if "timestamp" not in message or not message["timestamp"]:
        message["timestamp"] = datetime.now(timezone.utc).isoformat()
    elif isinstance(message["timestamp"], datetime):
        message["timestamp"] = message["timestamp"].isoformat()
    
    # Set read status to False for new messages
    if "read" not in message:
        message["read"] = False
        
    # Store in Firestore
    db.collection("messages").document(message["id"]).set(message)
    return message["id"]

async def get_unread_messages(user_id: str) -> List[dict]:
    """Retrieve all unread messages for a user"""
    try:
        # Modified query to avoid compound index issues
        recipient_msgs = (
            db.collection("messages")
            .where("recipient_id", "==", user_id)
            .get()
        )
        
        messages = []
        for msg in recipient_msgs:
            msg_dict = msg.to_dict()
            if not msg_dict.get("read", False):
                messages.append(msg_dict)
        
        # Sort by timestamp in Python
        messages.sort(key=lambda x: x.get("timestamp", 0))
        
        return messages
    except Exception as e:
        print(f"Error retrieving unread messages: {e}")
        return []

async def mark_message_as_read(message_id: str):
    """Mark a message as read"""
    db.collection("messages").document(message_id).update({"read": True})

async def update_user_status(user_id: str, online: bool):
    """Update a user's online status"""
    now = datetime.now(timezone.utc).timestamp()
    db.collection("recruiters").document(user_id).set(
        {"online": online, "last_seen": now},
        merge=True
    )

def update_user_status_sync(user_id: str, online: bool):
    """Non-async version for use in disconnect handler"""
    now = datetime.now(timezone.utc).timestamp()
    db.collection("recruiters").document(user_id).set(
        {"online": online, "last_seen": now},
        merge=True
    )

async def get_or_create_user(user_id: str, name: str = None) -> User:
    """Get a user or create if not exists"""
    user_ref = db.collection("recruiters").document(user_id)
    user = user_ref.get()
    
    if user.exists:
        user_data = user.to_dict()
        return User(**user_data)
    else:
        if not name:
            name = f"User_{user_id[:6]}"
        
        new_user = User(
            id=user_id,
            name=name,
            online=False,
            last_seen=datetime.now(timezone.utc).timestamp()
        )
        
        user_ref.set(new_user.dict())
        return new_user

async def get_user_conversations(user_id: str):
    """Get all conversations for a user"""
    try:
        # Get messages where user is sender or recipient - using two separate queries
        sent_msgs = (
            db.collection("messages")
            .where("sender_id", "==", user_id)
            .get()
        )
        
        received_msgs = (
            db.collection("messages")
            .where("recipient_id", "==", user_id)
            .get()
        )
        
        # Build a map of conversation partners with last message
        conversations = {}
        
        # Process sent messages
        for msg in sent_msgs:
            msg_data = msg.to_dict()
            # Check if recipient_id exists in the message data
            if "recipient_id" not in msg_data:
                print(f"Warning: Message missing recipient_id: {msg_data}")
                continue
                
            other_user = msg_data["recipient_id"]
            
            if other_user not in conversations:
                conversations[other_user] = {
                    "last_message": msg_data,
                    "unread_count": 0
                }
            elif msg_data.get("timestamp", 0) > conversations[other_user]["last_message"].get("timestamp", 0):
                conversations[other_user]["last_message"] = msg_data
        
        # Process received messages
        for msg in received_msgs:
            msg_data = msg.to_dict()
            # Check if sender_id exists in the message data
            if "sender_id" not in msg_data:
                print(f"Warning: Message missing sender_id: {msg_data}")
                continue
                
            other_user = msg_data["sender_id"]
            
            if other_user not in conversations:
                conversations[other_user] = {
                    "last_message": msg_data,
                    "unread_count": 0 if msg_data.get("read", False) else 1
                }
            else:
                if not msg_data.get("read", False):
                    conversations[other_user]["unread_count"] += 1
                
                if msg_data.get("timestamp", 0) > conversations[other_user]["last_message"].get("timestamp", 0):
                    conversations[other_user]["last_message"] = msg_data
        
        # Get user details for all conversation partners
        result = []
        for partner_id, conv_data in conversations.items():
            partner = await get_or_create_user(partner_id)
            result.append({
                "user": partner.dict(),
                "last_message": conv_data["last_message"],
                "unread_count": conv_data["unread_count"]
            })
        
        # Sort by timestamp - handle both ISO string and datetime objects
        def get_timestamp(x):
            timestamp = x["last_message"].get("timestamp")
            if isinstance(timestamp, str):
                try:
                    # Parse ISO format string and make it timezone-aware
                    dt = datetime.fromisoformat(timestamp)
                    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
                except ValueError:
                    return datetime.min.replace(tzinfo=timezone.utc)
            elif isinstance(timestamp, datetime):
                # Make naive datetime timezone-aware
                return timestamp if timestamp.tzinfo else timestamp.replace(tzinfo=timezone.utc)
            else:
                return datetime.min.replace(tzinfo=timezone.utc)
        
        result.sort(key=get_timestamp, reverse=True)
        return result
    except Exception as e:
        print(f"Error retrieving conversations: {e}")
        return []

async def get_conversation_history(user1_id: str, user2_id: str, limit: int = 50):
    """Get conversation history between two recruiters"""
    try:
        # Get messages in both directions
        msgs1 = (
            db.collection("messages")
            .where("sender_id", "==", user1_id)
            .where("recipient_id", "==", user2_id)
            .get()
        )
        
        msgs2 = (
            db.collection("messages")
            .where("sender_id", "==", user2_id)
            .where("recipient_id", "==", user1_id)
            .get()
        )
        
        # Combine and sort messages
        messages = []
        for msg in msgs1:
            messages.append(msg.to_dict())
        
        for msg in msgs2:
            messages.append(msg.to_dict())
        
        # Sort by timestamp in Python
        messages.sort(key=lambda x: x.get("timestamp", 0), reverse=True)
        
        # Limit to the requested number
        return messages[:limit]
    except Exception as e:
        print(f"Error retrieving conversation history: {e}")
        return []

# REST API endpoints for user management and message history
@app.post("/api/recruiters/")
async def create_user(user: User):
    db.collection("recruiters").document(user.id).set(user.dict())
    return {"message": "User created successfully", "user": user}

@app.get("/api/recruiters/{user_id}")
async def get_user(user_id: str):
    user = await get_or_create_user(user_id)
    return user

@app.get("/api/recruiters/")
async def get_recruiters():
    recruiters = db.collection("recruiters").get()
    return [user.to_dict() for user in recruiters]

@app.get("/api/conversations/{user_id}")
async def get_conversations(user_id: str):
    return await get_user_conversations(user_id)

@app.get("/api/messages/{user1_id}/{user2_id}")
async def get_messages(user1_id: str, user2_id: str, limit: int = 50):
    messages = await get_conversation_history(user1_id, user2_id, limit)
    return messages

@app.post("/api/messages/")
async def send_message(message: Message):
    message.id = str(uuid.uuid4())
    message.timestamp = datetime.now(timezone.utc)
    
    # Store message
    msg_dict = message.dict()
    await store_message(msg_dict)
    
    # Try to send to recipient if online
    if message.recipient_id in manager.active_connections:
        await manager.send_personal_message(msg_dict, message.recipient_id)
        await mark_message_as_read(message.id)
    
    return {"message": "Message sent", "message_id": message.id}

# WebSocket endpoint
@app.websocket("/ws/chat/{user_id}")
async def websocket_endpoint(websocket: WebSocket, user_id: str):
    await manager.connect(user_id, websocket)
    try:
        while True:
            data = await websocket.receive_json()
            print(f"Received WebSocket data: {data}")
            
            # Validate message
            if "recipient_id" not in data or "content" not in data:
                await websocket.send_json({"error": "Invalid message format. Must include recipient_id and content."})
                continue
            
            # Create message object
            message = {
                "id": str(uuid.uuid4()),
                "sender_id": user_id,
                "recipient_id": data["recipient_id"],
                "content": data["content"],
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "read": False
            }
            
            # Store and send message
            await manager.send_personal_message(message, data["recipient_id"])
            
            # Send confirmation back to sender
            await websocket.send_json({
                "type": "message_sent",
                "message": message
            })
            
    except WebSocketDisconnect:
        manager.disconnect(user_id)
    except Exception as e:
        print(f"WebSocket error: {e}")
        manager.disconnect(user_id)

# Serve static files (frontend)
app.mount("/", StaticFiles(directory="static", html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)