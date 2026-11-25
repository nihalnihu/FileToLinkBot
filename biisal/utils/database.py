# (c) @biisal
#(c) Adarsh-Goel
import datetime
import re
import motor.motor_asyncio


class Database:
    def __init__(self, uri, database_name):
        self._client = motor.motor_asyncio.AsyncIOMotorClient(uri)
        self.db = self._client[database_name]
        self.col = self.db.users
        self.bannedList = self.db.bannedList
        self.videos = self.db.videos

    def new_user(self, id, name: str = None, username: str = None):
        doc = dict(
            id=int(id),
            join_date=datetime.date.today().isoformat()
        )
        if name:
            doc["name"] = str(name).strip()
        if username:
            doc["username"] = str(username).strip().lstrip("@")
        return doc

    async def add_user(self, id, name: str = None, username: str = None):
        user = self.new_user(id, name=name, username=username)
        await self.col.update_one(
            {"id": int(id)},
            {"$setOnInsert": {"join_date": user["join_date"]}, "$set": {k: v for k, v in user.items() if k != "join_date"}},
            upsert=True,
        )

    async def update_user_profile(self, id, name: str = None, username: str = None):
        updates = {}
        if name is not None and str(name).strip():
            updates["name"] = str(name).strip()
        if username is not None:
            updates["username"] = str(username).strip().lstrip("@")
        if updates:
            await self.col.update_one({"id": int(id)}, {"$set": updates})
        
    async def add_user_pass(self, id, ag_pass):
        await self.add_user(int(id))
        await self.col.update_one({'id': int(id)}, {'$set': {'ag_p': ag_pass}})
    
    async def get_user_pass(self, id):
        user_pass = await self.col.find_one({'id': int(id)})
        return user_pass.get("ag_p", None) if user_pass else None
    
    async def is_user_exist(self, id):
        user = await self.col.find_one({'id': int(id)})
        return True if user else False

    async def total_users_count(self):
        count = await self.col.count_documents({})
        return count

    async def get_all_users(self):
        all_users = self.col.find({})
        return all_users

    async def delete_user(self, user_id):
        await self.col.delete_many({'id': int(user_id)})
        
    async def ban_user(self , user_id):
        user = await self.bannedList.find_one({'banId' : int(user_id)})
        if user:
            return False
        else:
            await self.bannedList.insert_one({'banId' : int(user_id)})
            return True
        
    async def is_banned(self , user_id):
        user = await self.bannedList.find_one({'banId' : int(user_id)})
        return True if user else False
    
    async def is_unbanned(self , user_id):
        try : 
            if await self.bannedList.find_one({'banId' : int(user_id)}):
                await self.bannedList.delete_one({'banId' : int(user_id)})
                return True
            else:
                return False
        except Exception as e:
            e = f'Fᴀɪʟᴇᴅ ᴛᴏ ᴜɴʙᴀɴ.Rᴇᴀsᴏɴ : {e}'
            print(e)
            return e

    async def add_video(self, video_doc: dict):
        msg_id = int(video_doc["msg_id"])
        await self.videos.update_one(
            {"msg_id": msg_id},
            {"$set": video_doc},
            upsert=True,
        )

    async def get_videos(
        self,
        query_text: str = None,
        tmdb_id: int = None,
        limit: int = 20,
        skip: int = 0,
    ):
        query = {}
        if tmdb_id is not None:
            query["tmdb_id"] = int(tmdb_id)
        if query_text and query_text.strip():
            safe_q = re.escape(query_text.strip())
            pattern = {"$regex": safe_q, "$options": "i"}
            query["$or"] = [
                {"title": pattern},
                {"file_name": pattern},
                {"stream_code": pattern},
            ]
        max_items = max(1, min(int(limit), 100))
        offset = max(0, int(skip))
        cursor = (
            self.videos.find(query, {"_id": 0})
            .sort("created_at", -1)
            .skip(offset)
            .limit(max_items)
        )
        return await cursor.to_list(length=max_items)

    async def get_video_by_code(self, code: str):
        raw = (code or "").strip()
        if not raw:
            return None
        normalized = raw.upper()
        doc = await self.videos.find_one({"stream_code": normalized}, {"_id": 0})
        if doc:
            return doc
        # Fallback if user pasted message id or partial code without MRDB- prefix
        m = re.match(r"^(?:MRDB-)?(\d+)-([A-Z0-9_-]{6})$", normalized)
        if m:
            doc = await self.videos.find_one(
                {"msg_id": int(m.group(1)), "hash": {"$regex": f"^{re.escape(m.group(2))}$", "$options": "i"}},
                {"_id": 0},
            )
            if doc:
                return doc
        return await self.videos.find_one(
            {"stream_code": {"$regex": f"^{re.escape(raw)}$", "$options": "i"}},
            {"_id": 0},
        )

    async def delete_video(self, msg_id: int = None, stream_code: str = None):
        if msg_id is not None:
            res = await self.videos.delete_many({"msg_id": int(msg_id)})
            if res.deleted_count > 0:
                return True
        if stream_code:
            res = await self.videos.delete_many(
                {"stream_code": stream_code.strip().upper()}
            )
            return res.deleted_count > 0
        return False

    async def total_banned_count(self):
        return await self.bannedList.count_documents({})

    async def total_videos_count(self):
        return await self.videos.count_documents({})

    async def get_banned_ids(self):
        cursor = self.bannedList.find({}, {"banId": 1, "_id": 0})
        docs = await cursor.to_list(length=5000)
        return {int(d["banId"]) for d in docs if d.get("banId") is not None}

    async def get_users_paginated(
        self,
        query_text: str = None,
        limit: int = 30,
        skip: int = 0,
    ):
        query = {}
        if query_text and query_text.strip():
            q = query_text.strip().lstrip("@")
            safe_q = re.escape(q)
            pattern = {"$regex": safe_q, "$options": "i"}
            or_clauses = [
                {"name": pattern},
                {"username": pattern},
                {"join_date": pattern},
            ]
            if q.isdigit():
                or_clauses.append({"id": int(q)})
            query["$or"] = or_clauses
        max_items = max(1, min(int(limit), 100))
        offset = max(0, int(skip))
        cursor = (
            self.col.find(query, {"_id": 0})
            .sort([("join_date", -1), ("id", -1)])
            .skip(offset)
            .limit(max_items)
        )
        return await cursor.to_list(length=max_items)
    

    
