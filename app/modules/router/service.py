from typing import Dict, Any
from uuid import UUID
from datetime import datetime

from flask import current_app

from app.modules.router.repository import RouterRepository, HotspotServerRepository
from app.models.router import Router, HotspotServer

from app.core.security.encryption import EncryptionService
from app.core.logging.logger import logger
from app.core.exceptions.handlers import NotFoundError, BusinessError
from app.integrations.mikrotik.client import MikroTikClient

from app.core.database.session import db


class RouterService:

    def __init__(self):
        self.repository = RouterRepository()
        self.encryption = EncryptionService()
        self.mikrotik_client = MikroTikClient()

    # ---------------- CREATE ----------------
    def create_router(self, organization_id: UUID, data: Dict[str, Any]) -> Router:
        encrypted_password = self.encryption.encrypt(data['password'])

        router_data = {
            'organization_id': organization_id,
            'network_id': data.get('network_id'),
            'name': data['name'],
            'model': data.get('model'),
            'ip_address': data['ip_address'],
            'api_port': data.get('api_port', 8728),
            'api_ssl_port': data.get('api_ssl_port', 8729),
            'username': data['username'],
            'password_encrypted': encrypted_password,
            'location': data.get('location'),
            'settings': data.get('settings', {})
        }

        router = self.repository.create(router_data)

        # optional background-safe call
        self.test_connection(router.id, organization_id)

        return router

    # ---------------- READ ----------------
    def get_router(self, router_id: UUID, organization_id: UUID) -> Router:
        router = self.repository.get_by_id(router_id, organization_id)
        if not router:
            raise NotFoundError("Router not found")
        return router

    # ---------------- UPDATE ----------------
    def update_router(self, router_id: UUID, organization_id: UUID, data: Dict[str, Any]) -> Router:
        if "password" in data:
            data["password_encrypted"] = self.encryption.encrypt(data.pop("password"))

        router = self.repository.update(router_id, organization_id, data)
        if not router:
            raise NotFoundError("Router not found")

        return router

    # ---------------- DELETE (SOFT) ----------------
    def delete_router(self, router_id: UUID, organization_id: UUID):
        router = self.repository.get_by_id(router_id, organization_id)
        if not router:
            raise NotFoundError("Router not found")

        self.repository.update(router_id, organization_id, {"is_active": False})

    # ---------------- CONNECTION TEST ----------------
    def test_connection(self, router_id: UUID, organization_id: UUID) -> Dict[str, Any]:
        router = self.get_router(router_id, organization_id)

        try:
            password = self.encryption.decrypt(router.password_encrypted)

            result = self.mikrotik_client.test_connection(
                host=str(router.ip_address),
                username=router.username,
                password=password,
                port=router.api_port
            )

            self.repository.update_status(
                router_id,
                organization_id,
                "online" if result.get("success") else "offline"
            )

            return result

        except Exception as e:
            self.repository.update_status(router_id, organization_id, "error")
            raise BusinessError(f"Connection test failed: {str(e)}")

    # ---------------- SYNC ----------------
    def sync_router(self, router_id: UUID, organization_id: UUID) -> Dict[str, Any]:
        router = self.get_router(router_id, organization_id)

        try:
            password = self.encryption.decrypt(router.password_encrypted)

            info = self.mikrotik_client.get_router_info(
                host=str(router.ip_address),
                username=router.username,
                password=password,
                port=router.api_port
            )

            self.repository.update(router_id, organization_id, {
                "model": info.get("model"),
                "firmware_version": info.get("version"),
                "last_sync_at": datetime.utcnow(),
                "status": "online"
            })

            self._sync_hotspot_servers(router, password)

            return {"success": True, "info": info}

        except Exception as e:
            self.repository.update_status(router_id, organization_id, "error")
            raise BusinessError(f"Sync failed: {str(e)}")

    # ---------------- HOTSPOT SYNC ----------------
    def _sync_hotspot_servers(self, router: Router, password: str):
        hotspot_servers = self.mikrotik_client.get_hotspot_servers(
            host=str(router.ip_address),
            username=router.username,
            password=password,
            port=router.api_port
        )

        for hs in hotspot_servers:
            existing = HotspotServer.query.filter_by(
                router_id=router.id,
                hotspot_id=hs["name"]
            ).first()

            if existing:
                existing.name = hs["name"]
                existing.interface = hs.get("interface")
                existing.is_active = hs.get("disabled") != "true"
            else:
                db.session.add(HotspotServer(
                    organization_id=router.organization_id,
                    router_id=router.id,
                    name=hs["name"],
                    hotspot_id=hs["name"],
                    interface=hs.get("interface"),
                    is_active=hs.get("disabled") != "true"
                ))

        db.session.commit()