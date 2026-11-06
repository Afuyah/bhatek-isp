"""performance indexes

Adds an index for every foreign-key column, plus composite indexes for the
tenant-scoped queries that run on every page load (subscription expiry,
live sessions by username, subscriber lookup by phone, invoice/transaction
lists by status, router fleet by status).

Guarded with a live inspector so it is safe on databases that already carry
some of these indexes, and so it can be re-run.

Revision ID: a1f2c3d4e5f6
Revises: f248ffbfc9be
"""

from alembic import op
import sqlalchemy as sa

revision = "a1f2c3d4e5f6"
down_revision = "f248ffbfc9be"
branch_labels = None
depends_on = None


def _indexes(table):
    """Names of indexes that already exist on ``table`` (empty if unknown)."""
    inspector = sa.inspect(op.get_bind())
    try:
        return {ix["name"] for ix in inspector.get_indexes(table)}
    except Exception:
        return set()


def _create(name, table, columns):
    if name in _indexes(table):
        return
    op.create_index(name, table, columns, unique=False)


def _drop(name, table):
    if name in _indexes(table):
        op.drop_index(name, table_name=table)


def upgrade():
    _create("ix_access_points_hotspot_server_id", 'access_points', ['hotspot_server_id'])
    _create("ix_access_points_router_id", 'access_points', ['router_id'])
    _create("ix_active_sessions_access_point_id", 'active_sessions', ['access_point_id'])
    _create("ix_active_sessions_hotspot_server_id", 'active_sessions', ['hotspot_server_id'])
    _create("ix_active_sessions_pppoe_server_id", 'active_sessions', ['pppoe_server_id'])
    _create("ix_active_sessions_router_id", 'active_sessions', ['router_id'])
    _create("ix_active_sessions_subscriber_id", 'active_sessions', ['subscriber_id'])
    _create("ix_active_sessions_subscription_id", 'active_sessions', ['subscription_id'])
    _create("ix_active_sessions_voucher_id", 'active_sessions', ['voucher_id'])
    _create("ix_audit_logs_organization_id", 'audit_logs', ['organization_id'])
    _create("ix_audit_logs_user_id", 'audit_logs', ['user_id'])
    _create("ix_devices_subscriber_id", 'devices', ['subscriber_id'])
    _create("ix_hotspot_servers_router_id", 'hotspot_servers', ['router_id'])
    _create("ix_invoice_items_invoice_id", 'invoice_items', ['invoice_id'])
    _create("ix_invoices_plan_id", 'invoices', ['plan_id'])
    _create("ix_invoices_subscriber_id", 'invoices', ['subscriber_id'])
    _create("ix_invoices_subscription_id", 'invoices', ['subscription_id'])
    _create("ix_nas_router_id", 'nas', ['router_id'])
    _create("ix_organization_settings_organization_id", 'organization_settings', ['organization_id'])
    _create("ix_organization_users_invited_by", 'organization_users', ['invited_by'])
    _create("ix_organization_users_organization_id", 'organization_users', ['organization_id'])
    _create("ix_organization_users_user_id", 'organization_users', ['user_id'])
    _create("ix_pppoe_servers_router_id", 'pppoe_servers', ['router_id'])
    _create("ix_refresh_tokens_user_id", 'refresh_tokens', ['user_id'])
    _create("ix_refunds_processed_by", 'refunds', ['processed_by'])
    _create("ix_refunds_transaction_id", 'refunds', ['transaction_id'])
    _create("ix_routers_nas_entry_id", 'routers', ['nas_entry_id'])
    _create("ix_routers_network_id", 'routers', ['network_id'])
    _create("ix_subscriptions_plan_id", 'subscriptions', ['plan_id'])
    _create("ix_subscriptions_subscriber_id", 'subscriptions', ['subscriber_id'])
    _create("ix_transactions_invoice_id", 'transactions', ['invoice_id'])
    _create("ix_transactions_payment_account_id", 'transactions', ['payment_account_id'])
    _create("ix_transactions_subscriber_id", 'transactions', ['subscriber_id'])
    _create("ix_transactions_subscription_id", 'transactions', ['subscription_id'])
    _create("ix_users_created_by", 'users', ['created_by'])
    _create("ix_users_organization_id", 'users', ['organization_id'])
    _create("ix_voucher_batches_created_by", 'voucher_batches', ['created_by'])
    _create("ix_voucher_batches_plan_id", 'voucher_batches', ['plan_id'])
    _create("ix_vouchers_batch_id", 'vouchers', ['batch_id'])
    _create("ix_vouchers_created_by", 'vouchers', ['created_by'])
    _create("ix_vouchers_plan_id", 'vouchers', ['plan_id'])
    _create("ix_vouchers_used_by_subscriber_id", 'vouchers', ['used_by_subscriber_id'])
    _create("ix_vouchers_used_on_router_id", 'vouchers', ['used_on_router_id'])
    _create("ix_subscriptions_organization_id_status", 'subscriptions', ['organization_id', 'status'])
    _create("ix_subscriptions_organization_id_expiry_time", 'subscriptions', ['organization_id', 'expiry_time'])
    _create("ix_active_sessions_organization_id_username", 'active_sessions', ['organization_id', 'username'])
    _create("ix_subscribers_organization_id_phone", 'subscribers', ['organization_id', 'phone'])
    _create("ix_subscribers_organization_id_subscriber_type", 'subscribers', ['organization_id', 'subscriber_type'])
    _create("ix_invoices_organization_id_status", 'invoices', ['organization_id', 'status'])
    _create("ix_transactions_organization_id_status", 'transactions', ['organization_id', 'status'])
    _create("ix_routers_organization_id_status", 'routers', ['organization_id', 'status'])


def downgrade():
    _drop("ix_access_points_hotspot_server_id", 'access_points')
    _drop("ix_access_points_router_id", 'access_points')
    _drop("ix_active_sessions_access_point_id", 'active_sessions')
    _drop("ix_active_sessions_hotspot_server_id", 'active_sessions')
    _drop("ix_active_sessions_pppoe_server_id", 'active_sessions')
    _drop("ix_active_sessions_router_id", 'active_sessions')
    _drop("ix_active_sessions_subscriber_id", 'active_sessions')
    _drop("ix_active_sessions_subscription_id", 'active_sessions')
    _drop("ix_active_sessions_voucher_id", 'active_sessions')
    _drop("ix_audit_logs_organization_id", 'audit_logs')
    _drop("ix_audit_logs_user_id", 'audit_logs')
    _drop("ix_devices_subscriber_id", 'devices')
    _drop("ix_hotspot_servers_router_id", 'hotspot_servers')
    _drop("ix_invoice_items_invoice_id", 'invoice_items')
    _drop("ix_invoices_plan_id", 'invoices')
    _drop("ix_invoices_subscriber_id", 'invoices')
    _drop("ix_invoices_subscription_id", 'invoices')
    _drop("ix_nas_router_id", 'nas')
    _drop("ix_organization_settings_organization_id", 'organization_settings')
    _drop("ix_organization_users_invited_by", 'organization_users')
    _drop("ix_organization_users_organization_id", 'organization_users')
    _drop("ix_organization_users_user_id", 'organization_users')
    _drop("ix_pppoe_servers_router_id", 'pppoe_servers')
    _drop("ix_refresh_tokens_user_id", 'refresh_tokens')
    _drop("ix_refunds_processed_by", 'refunds')
    _drop("ix_refunds_transaction_id", 'refunds')
    _drop("ix_routers_nas_entry_id", 'routers')
    _drop("ix_routers_network_id", 'routers')
    _drop("ix_subscriptions_plan_id", 'subscriptions')
    _drop("ix_subscriptions_subscriber_id", 'subscriptions')
    _drop("ix_transactions_invoice_id", 'transactions')
    _drop("ix_transactions_payment_account_id", 'transactions')
    _drop("ix_transactions_subscriber_id", 'transactions')
    _drop("ix_transactions_subscription_id", 'transactions')
    _drop("ix_users_created_by", 'users')
    _drop("ix_users_organization_id", 'users')
    _drop("ix_voucher_batches_created_by", 'voucher_batches')
    _drop("ix_voucher_batches_plan_id", 'voucher_batches')
    _drop("ix_vouchers_batch_id", 'vouchers')
    _drop("ix_vouchers_created_by", 'vouchers')
    _drop("ix_vouchers_plan_id", 'vouchers')
    _drop("ix_vouchers_used_by_subscriber_id", 'vouchers')
    _drop("ix_vouchers_used_on_router_id", 'vouchers')
    _drop("ix_subscriptions_organization_id_status", 'subscriptions')
    _drop("ix_subscriptions_organization_id_expiry_time", 'subscriptions')
    _drop("ix_active_sessions_organization_id_username", 'active_sessions')
    _drop("ix_subscribers_organization_id_phone", 'subscribers')
    _drop("ix_subscribers_organization_id_subscriber_type", 'subscribers')
    _drop("ix_invoices_organization_id_status", 'invoices')
    _drop("ix_transactions_organization_id_status", 'transactions')
    _drop("ix_routers_organization_id_status", 'routers')
