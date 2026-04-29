from typing import Dict, Any, Optional, List
from queue import Queue, Empty, Full
from threading import Lock
import time
from datetime import datetime

from app.core.logging.logger import logger
from app.integrations.mikrotik.client import MikroTikConnection, MikroTikAPIError

class ConnectionPool:
    """Connection pool for MikroTik routers"""
    
    def __init__(self, router_id: str, host: str, username: str, password: str,
                 port: int = 8728, use_ssl: bool = False, pool_size: int = 5,
                 max_retries: int = 3, retry_delay: int = 1):
        self.router_id = router_id
        self.host = host
        self.username = username
        self.password = password
        self.port = port
        self.use_ssl = use_ssl
        self.pool_size = pool_size
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        
        self._pool = Queue(maxsize=pool_size)
        self._active_connections = 0
        self._lock = Lock()
        self._closed = False
        
        # Initialize pool
        self._initialize_pool()
    
    def _initialize_pool(self):
        """Initialize connection pool"""
        for _ in range(self.pool_size):
            conn = self._create_connection()
            if conn:
                self._pool.put(conn)
            else:
                logger.warning(f"Failed to create initial connection for {self.host}")
    
    def _create_connection(self) -> Optional[MikroTikConnection]:
        """Create a new connection"""
        try:
            conn = MikroTikConnection(
                host=self.host,
                username=self.username,
                password=self.password,
                port=self.port,
                use_ssl=self.use_ssl
            )
            conn.connect()
            
            with self._lock:
                self._active_connections += 1
            
            logger.debug(f"Created connection to {self.host} (total: {self._active_connections})")
            return conn
        except Exception as e:
            logger.error(f"Failed to create connection to {self.host}: {e}")
            return None
    
    def get_connection(self, timeout: int = 30) -> MikroTikConnection:
        """Get a connection from the pool"""
        if self._closed:
            raise MikroTikAPIError("Connection pool is closed")
        
        try:
            conn = self._pool.get(timeout=timeout)
            
            # Check if connection is still valid
            if not conn.is_connected:
                logger.warning(f"Connection to {self.host} is stale, reconnecting")
                conn.disconnect()
                conn.connect()
            
            return conn
        except Empty:
            # Pool is empty, try to create a new connection
            with self._lock:
                if self._active_connections < self.pool_size * 2:
                    conn = self._create_connection()
                    if conn:
                        return conn
            
            raise MikroTikAPIError(f"No available connections to {self.host}")
    
    def return_connection(self, conn: MikroTikConnection):
        """Return connection to the pool"""
        if self._closed:
            conn.disconnect()
            return
        
        if conn.is_connected:
            try:
                self._pool.put(conn, timeout=5)
            except Full:
                logger.warning(f"Connection pool full for {self.host}, closing extra connection")
                conn.disconnect()
                with self._lock:
                    self._active_connections -= 1
        else:
            # Connection is dead, discard it
            with self._lock:
                self._active_connections -= 1
            
            # Create a replacement connection
            new_conn = self._create_connection()
            if new_conn:
                try:
                    self._pool.put(new_conn, timeout=5)
                except Full:
                    new_conn.disconnect()
                    with self._lock:
                        self._active_connections -= 1
    
    def execute(self, command: str, retries: int = None, **kwargs) -> List[Dict[str, Any]]:
        """Execute command using connection from pool"""
        retries = retries or self.max_retries
        last_error = None
        
        for attempt in range(retries):
            conn = None
            try:
                conn = self.get_connection()
                result = conn.execute(command, **kwargs)
                self.return_connection(conn)
                return result
            except (MikroTikAPIError, ConnectionError, TimeoutError) as e:
                last_error = e
                if conn:
                    self.return_connection(conn)
                
                if attempt < retries - 1:
                    logger.warning(f"Command failed (attempt {attempt + 1}/{retries}): {e}")
                    time.sleep(self.retry_delay * (2 ** attempt))
                else:
                    logger.error(f"Command failed after {retries} attempts: {e}")
        
        raise MikroTikAPIError(f"Command failed: {last_error}")
    
    def health_check(self):
        """Check health of all connections in the pool"""
        temp_conns = []
        healthy_count = 0
        
        # Get all connections
        while not self._pool.empty():
            try:
                conn = self._pool.get_nowait()
                temp_conns.append(conn)
            except Empty:
                break
        
        # Test each connection
        for conn in temp_conns:
            if conn.ping():
                healthy_count += 1
                self._pool.put(conn)
            else:
                logger.warning(f"Unhealthy connection to {self.host}, reconnecting")
                conn.disconnect()
                new_conn = self._create_connection()
                if new_conn:
                    self._pool.put(new_conn)
                else:
                    with self._lock:
                        self._active_connections -= 1
        
        logger.info(f"Health check for {self.host}: {healthy_count}/{len(temp_conns)} healthy")
        
        # Top up pool if needed
        current_size = self._pool.qsize()
        for _ in range(self.pool_size - current_size):
            conn = self._create_connection()
            if conn:
                self._pool.put(conn)
    
    def close(self):
        """Close all connections in the pool"""
        self._closed = True
        
        while not self._pool.empty():
            try:
                conn = self._pool.get_nowait()
                conn.disconnect()
            except Empty:
                break
        
        logger.info(f"Closed connection pool for {self.host}")
    
    def stats(self) -> Dict[str, Any]:
        """Get pool statistics"""
        return {
            'router_id': self.router_id,
            'host': self.host,
            'pool_size': self.pool_size,
            'active_connections': self._active_connections,
            'available_connections': self._pool.qsize(),
            'closed': self._closed
        }

class PoolManager:
    """Manager for multiple connection pools"""
    
    def __init__(self):
        self._pools = {}
        self._lock = Lock()
    
    def get_pool(self, router_id: str, router_data: Dict[str, Any]) -> ConnectionPool:
        """Get or create connection pool for router"""
        with self._lock:
            if router_id not in self._pools:
                pool = ConnectionPool(
                    router_id=router_id,
                    host=router_data['ip_address'],
                    username=router_data['username'],
                    password=router_data.get('password_encrypted', ''),
                    port=router_data.get('api_port', 8728),
                    use_ssl=router_data.get('api_ssl', False),
                    pool_size=router_data.get('connection_pool_size', 5)
                )
                self._pools[router_id] = pool
            return self._pools[router_id]
    
    def execute(self, router_id: str, router_data: Dict[str, Any],
                command: str, **kwargs) -> List[Dict[str, Any]]:
        """Execute command using appropriate pool"""
        pool = self.get_pool(router_id, router_data)
        return pool.execute(command, **kwargs)
    
    def health_check_all(self):
        """Run health check on all pools"""
        for router_id, pool in self._pools.items():
            try:
                pool.health_check()
            except Exception as e:
                logger.error(f"Health check failed for {router_id}: {e}")
    
    def close_all(self):
        """Close all pools"""
        with self._lock:
            for router_id, pool in self._pools.items():
                try:
                    pool.close()
                except:
                    pass
            self._pools.clear()