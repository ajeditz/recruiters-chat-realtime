# from fastapi import FastAPI, WebSocket, WebSocketDisconnect
# from typing import Dict, List
# from fastapi.middleware.cors import CORSMiddleware
# from firebase_admin import credentials, firestore, messaging
# import firebase_admin
# import os 
# from dotenv import load_dotenv
# load_dotenv()


# cred = credentials.Certificate(os.getenv("CRED_PATH"))
# firebase_admin.initialize_app(cred)
# db = firestore.client()

# app = FastAPI()

# app.add_middleware(
#     CORSMiddleware,
#     allow_origins=["*"],  # Allows all origins. Replace with a list of origins to restrict.
#     allow_credentials=True,
#     allow_methods=["*"],  # Allows all HTTP methods. Adjust as needed.
#     allow_headers=["*"],  # Allows all headers. Adjust as needed.
# )


# from google.cloud import firestore

# class ConnectionManager:
#     def __init__(self):
#         self.active_connections: Dict[str, WebSocket] = {}

#     async def connect(self, user_id: str, websocket: WebSocket):
#         await websocket.accept()
#         self.active_connections[user_id] = websocket
        
#         # Fetch unread messages from Firestore
#         messages_ref = db.collection("messages").where("receiver_id", "==", user_id).where("status", "==", "unread")
#         unread_messages = messages_ref.stream()

#         for message in unread_messages:
#             await websocket.send_json(message.to_dict())
#             db.collection("messages").document(message.id).update({"status": "read"})  # Mark as read

#     def disconnect(self, user_id: str):
#         if user_id in self.active_connections:
#             del self.active_connections[user_id]

#     async def send_or_store_message(self, sender_id: str, receiver_id: str, message: str):
#         message_data = {
#             "sender_id": sender_id,
#             "receiver_id": receiver_id,
#             "content": message,
#             "status": "unread",  # Mark as unread
#             "timestamp": firestore.SERVER_TIMESTAMP,
#         }

#         if receiver_id in self.active_connections:
#             await self.active_connections[receiver_id].send_json(message_data)
#         else:
#             db.collection("messages").add(message_data)  # Store unread message in Firestore

# manager=ConnectionManager()

# @app.websocket("/ws/chat/{user_id}")
# async def chat_endpoint(websocket: WebSocket, user_id: str):
#     await manager.connect(user_id, websocket)
#     try:
#         while True:
#             data = await websocket.receive_json()
#             receiver_id = data["receiver_id"]
#             message_text = data["message"]

#             message_data = {
#                 "sender_id": user_id,
#                 "receiver_id": receiver_id,
#                 "content": message_text,
#             }

#             await manager.send_or_store_message(user_id, receiver_id, message_text)
#     except WebSocketDisconnect:
#         manager.disconnect(user_id)

# def send_push_notification(receiver_id: str, message: str):
#     user_doc = db.collection("recruiters").document(receiver_id).get()
#     if user_doc.exists:
#         fcm_token = user_doc.to_dict().get("fcm_token")  # Store user's device token in Firestore
#         if fcm_token:
#             notification = messaging.Message(
#                 notification=messaging.Notification(
#                     title="New Message",
#                     body=message
#                 ),
#                 token=fcm_token
#             )
#             messaging.send(notification)

