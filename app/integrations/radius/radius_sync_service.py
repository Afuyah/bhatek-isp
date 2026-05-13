"""
RADIUS Sync Service - Synchronizes subscribers and subscriptions to FreeRADIUS database
This service manages the radcheck, radreply, and radusergroup tables
"""
from typing import Dict, Any, Optional, List
from uuid import UUID
from datetime import datetime
from sqlalchemy import and_, or_, delete
from sqlalchemy.orm import Session

from app.core.database.session import db
from app.core.logging.logger import logger
from app.core.security.encryption import EncryptionService
from app.modules.subscriber.service import SubscriberService
from app.modules.billing.service import BillingService
from app.integrations.radius.dictionary import MikroTikDictionary


class RadiusSyncService:
    """
    Service to sync subscribers and subscriptions to FreeRADIUS database
    Manages radcheck, radreply, and radusergroup tables
    """
    
    def __init__(self):
        self.encryption = EncryptionService()
        self.subscriber_service = SubscriberService()
        self.billing_service = BillingService()
    
   
    
    def sync_hotspot_user_to_radius(self, subscriber, subscription, plan=None) -> bool:
        """
        Sync a hotspot user to RADIUS tables
        
        For hotspot users: username = phone number, password = subscription ID
        
        Args:
            subscriber: Subscriber model object
            subscription: Subscription model object
            plan: Plan model object (optional, will use subscription.plan if not provided)
        
        Returns:
            True if successful, False otherwise
        """
        try:
            if not subscription:
                logger.warning(f"No subscription for hotspot subscriber {subscriber.id}")
                return False
            
            # Hotspot user - phone number is username
            username = subscriber.phone
            # For hotspot, password is subscription ID
            password = str(subscription.id)
            
            if not username:
                logger.error(f"No phone number for hotspot subscriber {subscriber.id}")
                return False
            
            # Sync to radcheck
            self._sync_radcheck(username, password, subscriber.organization_id)
            
            # Sync to radreply (bandwidth limits, timeouts)
            radius_attrs = self._get_radius_attributes(subscription, plan)
            self._sync_radreply(username, radius_attrs, subscriber.organization_id)
            
            # Sync to radusergroup
            groupname = f"hotspot_{subscription.plan.plan_type if subscription.plan else 'default'}"
            self._sync_radusergroup(username, groupname, subscriber.organization_id)
            
            logger.info(f"Synced hotspot user {username} to RADIUS")
            return True
            
        except Exception as e:
            logger.error(f"Error syncing hotspot user to RADIUS: {e}", exc_info=True)
            return False
    
    def sync_pppoe_user_to_radius(self, subscriber, password: str = None, 
                                   subscription=None, plan=None) -> bool:
        """
        Sync a PPPoE user to RADIUS tables
        
        For PPPoE users: username = custom username, password = user-provided password
        
        Args:
            subscriber: Subscriber model object
            password: Plaintext password (if not provided, will decrypt from stored)
            subscription: Subscription model object (optional)
            plan: Plan model object (optional)
        
        Returns:
            True if successful, False otherwise
        """
        try:
            # Get active subscription if not provided
            if not subscription:
                subscription = self.subscriber_service.get_active_subscription(
                    subscriber.id, subscriber.organization_id
                )
            
            if not subscription:
                logger.warning(f"No active subscription for PPPoE subscriber {subscriber.id}")
                return False
            
            # PPPoE user - username is custom username
            username = subscriber.username
            
            if not username:
                logger.error(f"No username for PPPoE subscriber {subscriber.id}")
                return False
            
            # Get password
            if not password:
                # Try to decrypt stored password
                if subscriber.password_encrypted:
                    password = self.encryption.decrypt(subscriber.password_encrypted)
                else:
                    logger.error(f"No password for PPPoE subscriber {subscriber.id}")
                    return False
            
            # Sync to radcheck
            self._sync_radcheck(username, password, subscriber.organization_id)
            
            # Sync to radreply (bandwidth limits, timeouts)
            radius_attrs = self._get_radius_attributes(subscription, plan)
            self._sync_radreply(username, radius_attrs, subscriber.organization_id)
            
            # Sync to radusergroup
            groupname = f"pppoe_{subscription.plan.plan_type if subscription.plan else 'default'}"
            self._sync_radusergroup(username, groupname, subscriber.organization_id)
            
            logger.info(f"Synced PPPoE user {username} to RADIUS")
            return True
            
        except Exception as e:
            logger.error(f"Error syncing PPPoE user to RADIUS: {e}", exc_info=True)
            return False
    
    def update_subscription_in_radius(self, subscriber, subscription, plan=None) -> bool:
        """
        Update RADIUS attributes when subscription is renewed or changed
        
        Args:
            subscriber: Subscriber model object
            subscription: Subscription model object
            plan: Plan model object (optional)
        
        Returns:
            True if successful, False otherwise
        """
        try:
            # Determine username based on subscriber type
            if subscriber.subscriber_type == 'pppoe':
                username = subscriber.username
            else:
                username = subscriber.phone
            
            if not username:
                logger.error(f"No username for subscriber {subscriber.id}")
                return False
            
            # Get updated RADIUS attributes
            radius_attrs = self._get_radius_attributes(subscription, plan)
            
            # Update radreply (remove old, add new)
            self._sync_radreply(username, radius_attrs, subscriber.organization_id)
            
            # For hotspot, also update password if subscription ID changed (on renewal)
            if subscriber.subscriber_type == 'hotspot':
                new_password = str(subscription.id)
                self._sync_radcheck(username, new_password, subscriber.organization_id)
            
            # Update group if changed
            groupname = f"{subscriber.subscriber_type}_{subscription.plan.plan_type if subscription.plan else 'default'}"
            self._sync_radusergroup(username, groupname, subscriber.organization_id)
            
            logger.info(f"Updated RADIUS for subscriber {username}")
            return True
            
        except Exception as e:
            logger.error(f"Error updating subscription in RADIUS: {e}", exc_info=True)
            return False
    
    # ==========================================================================
    # LEGACY / GENERIC SYNC METHOD (Kept for backward compatibility)
    # ==========================================================================
    
    def sync_subscriber_to_radius(self, subscriber, subscription=None, password: str = None) -> bool:
        """
        Generic sync method - dispatches to type-specific methods
        
        For hotspot users: username = phone number, password = subscription ID
        For PPPoE users: username = custom username, password = user-provided password
        
        Args:
            subscriber: Subscriber model object
            subscription: Subscription model object (optional, will fetch if not provided)
            password: For PPPoE users only - the password to set
        
        Returns:
            True if successful, False otherwise
        """
        try:
            # Get active subscription if not provided
            if not subscription:
                subscription = self.subscriber_service.get_active_subscription(
                    subscriber.id, subscriber.organization_id
                )
            
            if not subscription:
                logger.warning(f"No active subscription for subscriber {subscriber.id}")
                return False
            
            # Dispatch to type-specific method
            if subscriber.subscriber_type == 'hotspot':
                return self.sync_hotspot_user_to_radius(subscriber, subscription)
            else:  # pppoe
                return self.sync_pppoe_user_to_radius(subscriber, password, subscription)
            
        except Exception as e:
            logger.error(f"Error syncing subscriber to RADIUS: {e}", exc_info=True)
            return False
    
    # ==========================================================================
    # INTERNAL RADIUS TABLE OPERATIONS
    # ==========================================================================
    
    def _sync_radcheck(self, username: str, password: str, organization_id: UUID) -> None:
        """
        Sync authentication to radcheck table
        """
        try:
            from app.models.radius import RadCheck
            
            # Remove existing entries for this user
            RadCheck.query.filter(
                and_(
                    RadCheck.username == username,
                    RadCheck.organization_id == organization_id
                )
            ).delete()
            
            # Add new authentication entry
            radcheck = RadCheck(
                username=username,
                attribute='Cleartext-Password',
                op=':=',
                value=password,
                organization_id=organization_id
            )
            db.session.add(radcheck)
            db.session.commit()
            
            logger.debug(f"Synced radcheck for {username}")
            
        except ImportError:
            # If model doesn't exist yet, use raw SQL
            logger.warning("RadCheck model not found, using raw SQL")
            self._sync_radcheck_raw(username, password, organization_id)
    
    def _sync_radcheck_raw(self, username: str, password: str, organization_id: UUID) -> None:
        """Raw SQL version for radcheck sync"""
        try:
            # Delete existing
            db.session.execute(
                "DELETE FROM radcheck WHERE username = :username AND organization_id = :org_id",
                {'username': username, 'org_id': str(organization_id)}
            )
            
            # Insert new
            db.session.execute(
                """INSERT INTO radcheck (username, attribute, op, value, organization_id)
                   VALUES (:username, 'Cleartext-Password', ':=', :password, :org_id)""",
                {'username': username, 'password': password, 'org_id': str(organization_id)}
            )
            db.session.commit()
            
        except Exception as e:
            db.session.rollback()
            logger.error(f"Raw radcheck sync failed: {e}")
    
    def _get_radius_attributes(self, subscription, plan=None) -> List[Dict[str, Any]]:
        """
        Generate RADIUS reply attributes from subscription
        """
        # Use provided plan or from subscription
        plan_obj = plan or subscription.plan
        
        attributes = []
        
        # Session timeout
        if plan_obj.session_timeout_seconds:
            attributes.append({
                'attribute': 'Session-Timeout',
                'op': ':=',
                'value': str(int(plan_obj.session_timeout_seconds))
            })
        else:
            # Default 24 hours
            attributes.append({
                'attribute': 'Session-Timeout',
                'op': ':=',
                'value': '86400'
            })
        
        # Idle timeout
        if plan_obj.idle_timeout_seconds:
            attributes.append({
                'attribute': 'Idle-Timeout',
                'op': ':=',
                'value': str(int(plan_obj.idle_timeout_seconds))
            })
        else:
            # Default 5 minutes
            attributes.append({
                'attribute': 'Idle-Timeout',
                'op': ':=',
                'value': '300'
            })
        
        # Bandwidth limits (prefer subscription overrides)
        bandwidth_up = subscription.bandwidth_up_mbps or plan_obj.bandwidth_up_mbps or 0
        bandwidth_down = subscription.bandwidth_down_mbps or plan_obj.bandwidth_down_mbps or 0
        
        if bandwidth_up > 0 or bandwidth_down > 0:
            rate_limit = MikroTikDictionary.format_rate_limit(
                upload=bandwidth_up if bandwidth_up > 0 else bandwidth_down,
                download=bandwidth_down if bandwidth_down > 0 else bandwidth_up,
                unit="M"
            )
            attributes.append({
                'attribute': 'Mikrotik-Rate-Limit',
                'op': ':=',
                'value': rate_limit
            })
        
        # Data cap (if data-based plan)
        if plan_obj.validity_type == 'data_based' and plan_obj.data_limit_mb:
            total_limit_bytes = int(plan_obj.data_limit_mb) * 1024 * 1024
            attributes.append({
                'attribute': 'Mikrotik-Total-Limit',
                'op': ':=',
                'value': str(total_limit_bytes)
            })
        
        # Concurrent logins / device limit
        device_limit = subscription.device_limit or plan_obj.concurrent_logins or 1
        if device_limit > 1:
            attributes.append({
                'attribute': 'Simultaneous-Use',
                'op': ':=',
                'value': str(device_limit)
            })
        
        return attributes
    
    def _sync_radreply(self, username: str, attributes: List[Dict[str, Any]], organization_id: UUID) -> None:
        """
        Sync reply attributes to radreply table
        """
        try:
            from app.models.radius import RadReply
            
            # Remove existing entries
            RadReply.query.filter(
                and_(
                    RadReply.username == username,
                    RadReply.organization_id == organization_id
                )
            ).delete()
            
            # Add new attributes
            for attr in attributes:
                radreply = RadReply(
                    username=username,
                    attribute=attr['attribute'],
                    op=attr['op'],
                    value=attr['value'],
                    organization_id=organization_id
                )
                db.session.add(radreply)
            
            db.session.commit()
            logger.debug(f"Synced radreply for {username}: {len(attributes)} attributes")
            
        except ImportError:
            self._sync_radreply_raw(username, attributes, organization_id)
    
    def _sync_radreply_raw(self, username: str, attributes: List[Dict[str, Any]], organization_id: UUID) -> None:
        """Raw SQL version for radreply sync"""
        try:
            # Delete existing
            db.session.execute(
                "DELETE FROM radreply WHERE username = :username AND organization_id = :org_id",
                {'username': username, 'org_id': str(organization_id)}
            )
            
            # Insert new attributes
            for attr in attributes:
                db.session.execute(
                    """INSERT INTO radreply (username, attribute, op, value, organization_id)
                       VALUES (:username, :attribute, :op, :value, :org_id)""",
                    {
                        'username': username,
                        'attribute': attr['attribute'],
                        'op': attr['op'],
                        'value': attr['value'],
                        'org_id': str(organization_id)
                    }
                )
            db.session.commit()
            
        except Exception as e:
            db.session.rollback()
            logger.error(f"Raw radreply sync failed: {e}")
    
    def _sync_radusergroup(self, username: str, groupname: str, organization_id: UUID) -> None:
        """
        Sync user group to radusergroup table
        """
        try:
            from app.models.radius import RadUserGroup
            
            # Remove existing
            RadUserGroup.query.filter(
                and_(
                    RadUserGroup.username == username,
                    RadUserGroup.organization_id == organization_id
                )
            ).delete()
            
            # Add new group
            radusergroup = RadUserGroup(
                username=username,
                groupname=groupname,
                priority=1,
                organization_id=organization_id
            )
            db.session.add(radusergroup)
            db.session.commit()
            
            logger.debug(f"Synced radusergroup for {username} to {groupname}")
            
        except ImportError:
            self._sync_radusergroup_raw(username, groupname, organization_id)
    
    def _sync_radusergroup_raw(self, username: str, groupname: str, organization_id: UUID) -> None:
        """Raw SQL version for radusergroup sync"""
        try:
            db.session.execute(
                "DELETE FROM radusergroup WHERE username = :username AND organization_id = :org_id",
                {'username': username, 'org_id': str(organization_id)}
            )
            
            db.session.execute(
                """INSERT INTO radusergroup (username, groupname, priority, organization_id)
                   VALUES (:username, :groupname, 1, :org_id)""",
                {'username': username, 'groupname': groupname, 'org_id': str(organization_id)}
            )
            db.session.commit()
            
        except Exception as e:
            db.session.rollback()
            logger.error(f"Raw radusergroup sync failed: {e}")
    
    # ==========================================================================
    # REMOVE SUBSCRIBER FROM RADIUS
    # ==========================================================================
    
    def remove_subscriber_from_radius(self, subscriber) -> bool:
        """
        Remove a subscriber from all RADIUS tables
        """
        try:
            # Get username based on subscriber type
            if subscriber.subscriber_type == 'pppoe':
                username = subscriber.username
            else:
                username = subscriber.phone
            
            if not username:
                logger.warning(f"No username for subscriber {subscriber.id}")
                return False
            
            # Remove from radcheck
            self._remove_radcheck(username, subscriber.organization_id)
            
            # Remove from radreply
            self._remove_radreply(username, subscriber.organization_id)
            
            # Remove from radusergroup
            self._remove_radusergroup(username, subscriber.organization_id)
            
            logger.info(f"Removed subscriber {username} from RADIUS")
            return True
            
        except Exception as e:
            logger.error(f"Error removing subscriber from RADIUS: {e}", exc_info=True)
            return False
    
    def _remove_radcheck(self, username: str, organization_id: UUID) -> None:
        """Remove user from radcheck"""
        try:
            from app.models.radius import RadCheck
            RadCheck.query.filter(
                and_(
                    RadCheck.username == username,
                    RadCheck.organization_id == organization_id
                )
            ).delete()
            db.session.commit()
        except ImportError:
            db.session.execute(
                "DELETE FROM radcheck WHERE username = :username AND organization_id = :org_id",
                {'username': username, 'org_id': str(organization_id)}
            )
            db.session.commit()
    
    def _remove_radreply(self, username: str, organization_id: UUID) -> None:
        """Remove user from radreply"""
        try:
            from app.models.radius import RadReply
            RadReply.query.filter(
                and_(
                    RadReply.username == username,
                    RadReply.organization_id == organization_id
                )
            ).delete()
            db.session.commit()
        except ImportError:
            db.session.execute(
                "DELETE FROM radreply WHERE username = :username AND organization_id = :org_id",
                {'username': username, 'org_id': str(organization_id)}
            )
            db.session.commit()
    
    def _remove_radusergroup(self, username: str, organization_id: UUID) -> None:
        """Remove user from radusergroup"""
        try:
            from app.models.radius import RadUserGroup
            RadUserGroup.query.filter(
                and_(
                    RadUserGroup.username == username,
                    RadUserGroup.organization_id == organization_id
                )
            ).delete()
            db.session.commit()
        except ImportError:
            db.session.execute(
                "DELETE FROM radusergroup WHERE username = :username AND organization_id = :org_id",
                {'username': username, 'org_id': str(organization_id)}
            )
            db.session.commit()
    
    # ==========================================================================
    # BULK SYNC OPERATIONS
    # ==========================================================================
    
    def sync_all_active_subscribers(self, organization_id: UUID) -> Dict[str, Any]:
        """
        Sync all active subscribers for an organization to RADIUS
        
        Returns: Dict with success/failure counts
        """
        result = {
            'total': 0,
            'synced': 0,
            'failed': 0,
            'errors': []
        }
        
        try:
            # Get all active subscribers with active subscriptions
            subscribers = self.subscriber_service.get_organization_subscribers(
                organization_id=organization_id,
                skip=0,
                limit=10000,
                filters={'status': 'active'}
            )
            
            result['total'] = len(subscribers)
            
            for subscriber in subscribers:
                try:
                    subscription = self.subscriber_service.get_active_subscription(
                        subscriber.id, organization_id
                    )
                    
                    if subscription:
                        if self.sync_subscriber_to_radius(subscriber, subscription):
                            result['synced'] += 1
                        else:
                            result['failed'] += 1
                            result['errors'].append({
                                'id': str(subscriber.id),
                                'name': subscriber.display_name,
                                'error': 'Sync failed'
                            })
                except Exception as e:
                    result['failed'] += 1
                    result['errors'].append({
                        'id': str(subscriber.id),
                        'name': subscriber.display_name,
                        'error': str(e)
                    })
            
            logger.info(f"RADIUS sync completed: {result['synced']}/{result['total']} synced")
            return result
            
        except Exception as e:
            logger.error(f"Error in bulk sync: {e}", exc_info=True)
            result['error'] = str(e)
            return result
    
    # ==========================================================================
    # VERIFICATION
    # ==========================================================================
    
    def verify_radius_sync(self, subscriber) -> Dict[str, Any]:
        """
        Verify if a subscriber is correctly synced to RADIUS
        """
        try:
            if subscriber.subscriber_type == 'pppoe':
                username = subscriber.username
            else:
                username = subscriber.phone
            
            if not username:
                return {'synced': False, 'error': 'No username'}
            
            # Check radcheck
            from app.models.radius import RadCheck, RadReply
            
            radcheck = RadCheck.query.filter(
                and_(
                    RadCheck.username == username,
                    RadCheck.organization_id == subscriber.organization_id
                )
            ).first()
            
            # Check radreply
            radreply_count = RadReply.query.filter(
                and_(
                    RadReply.username == username,
                    RadReply.organization_id == subscriber.organization_id
                )
            ).count()
            
            return {
                'synced': radcheck is not None,
                'username': username,
                'has_auth': radcheck is not None,
                'reply_attributes': radreply_count
            }
            
        except Exception as e:
            logger.error(f"Error verifying RADIUS sync: {e}")
            return {'synced': False, 'error': str(e)}