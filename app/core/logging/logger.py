import logging
import json
import sys
from datetime import datetime
from pythonjsonlogger import jsonlogger
from flask import has_request_context, request, g
import traceback

class CustomJsonFormatter(jsonlogger.JsonFormatter):
  
    def add_fields(self, log_record, record, message_dict):
        super().add_fields(log_record, record, message_dict)
        
        # Add timestamp
        log_record['timestamp'] = datetime.utcnow().isoformat()
        
        # Add request context if available
        if has_request_context():
            log_record['request_id'] = str(g.get('request_id', ''))
            log_record['user_id'] = str(g.get('user_id', ''))
            log_record['organization_id'] = str(g.get('organization_id', ''))
            log_record['ip'] = request.remote_addr
            log_record['path'] = request.path
            log_record['method'] = request.method
        
        # Add exception info if present
        if record.exc_info:
            log_record['exception'] = ''.join(traceback.format_exception(*record.exc_info))

def setup_logging(app):
    """Setup application logging"""
    log_level = getattr(logging, app.config.get('LOG_LEVEL', 'INFO'))
    log_format = app.config.get('LOG_FORMAT', 'json')
    
    # Clear existing handlers
    root_logger = logging.getLogger()
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    
    # Create console handler
    console_handler = logging.StreamHandler(sys.stdout)
    
    if log_format == 'json':
        formatter = CustomJsonFormatter('%(timestamp)s %(levelname)s %(name)s %(message)s')
    else:
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
    
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)
    root_logger.setLevel(log_level)
    
    # Set specific loggers
    logging.getLogger('sqlalchemy.engine').setLevel(logging.WARNING)
    logging.getLogger('urllib3').setLevel(logging.WARNING)
    
    app.logger.info("Logging configured", extra={
        'log_level': app.config['LOG_LEVEL'],
        'log_format': log_format
    })

logger = logging.getLogger('isp-platform')
