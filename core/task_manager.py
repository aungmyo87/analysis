"""
Task Manager
Handles task creation, status tracking, and result storage
"""

import asyncio
import uuid
import time
import logging
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime, timedelta
import threading

logger = logging.getLogger(__name__)


class TaskStatus(Enum):
    """Task status enumeration"""
    PENDING = "pending"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"
    EXPIRED = "expired"


class TaskType(Enum):
    """reCAPTCHA task types"""
    NORMAL_V2 = "RecaptchaV2Task"
    NORMAL_V2_PROXYLESS = "RecaptchaV2TaskProxyless"
    ENTERPRISE_V2 = "RecaptchaV2EnterpriseTask"
    ENTERPRISE_V2_PROXYLESS = "RecaptchaV2EnterpriseTaskProxyless"


@dataclass
class Task:
    """Represents a captcha solving task"""
    id: str
    task_type: str
    website_url: str
    website_key: str
    recaptcha_type: str  # normal | invisible | enterprise
    status: TaskStatus = TaskStatus.PENDING
    
    # Optional parameters
    proxy: Optional[Dict] = None
    user_agent: Optional[str] = None
    cookies: Optional[str] = None
    is_invisible: bool = False
    page_action: Optional[str] = None
    enterprise_payload: Optional[Dict] = None
    api_domain: Optional[str] = None
    
    # Result
    solution: Optional[Dict] = None
    error_id: int = 0
    error_message: Optional[str] = None
    
    # Metadata
    client_key: str = ""
    create_time: float = field(default_factory=time.time)
    start_time: Optional[float] = None
    end_time: Optional[float] = None
    solve_count: int = 0
    cost: float = 0.0
    ip: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert task to dictionary"""
        return {
            "id": self.id,
            "taskType": self.task_type,
            "websiteURL": self.website_url,
            "websiteKey": self.website_key,
            "recaptchaType": self.recaptcha_type,
            "status": self.status.value,
            "isInvisible": self.is_invisible,
            "createTime": int(self.create_time),
            "endTime": int(self.end_time) if self.end_time else None,
            "solveCount": self.solve_count,
            "cost": str(self.cost),
        }
    
    def get_result(self) -> Dict[str, Any]:
        """Get task result in API format"""
        result = {
            "errorId": self.error_id,
            "status": self.status.value if self.status != TaskStatus.READY else "ready",
        }
        
        if self.status == TaskStatus.READY and self.solution:
            result["solution"] = self.solution
            result["cost"] = str(self.cost)
            result["createTime"] = int(self.create_time)
            result["endTime"] = int(self.end_time) if self.end_time else int(time.time())
            result["solveCount"] = self.solve_count
            if self.ip:
                result["ip"] = self.ip
        
        elif self.status == TaskStatus.FAILED:
            result["errorId"] = self.error_id or 14
            result["errorMessage"] = self.error_message or "Task failed"
        
        elif self.status == TaskStatus.PROCESSING:
            result["status"] = "processing"
        
        return result


class TaskManager:
    """
    Manages the lifecycle of captcha solving tasks.
    Provides task creation, status tracking, and cleanup.
    """
    
    def __init__(self, max_tasks: int = 10000, task_ttl: int = 300):
        """
        Initialize task manager.
        
        Args:
            max_tasks: Maximum number of tasks to keep in memory
            task_ttl: Time-to-live for completed tasks in seconds
        """
        self._tasks: Dict[str, Task] = {}
        self._lock = threading.Lock()
        self._max_tasks = max_tasks
        self._task_ttl = task_ttl
        
        # Stats
        self._total_created = 0
        self._total_completed = 0
        self._total_failed = 0
    
    def create_task(
        self,
        task_type: str,
        website_url: str,
        website_key: str,
        recaptcha_type: str = "normal",
        client_key: str = "",
        **kwargs
    ) -> Task:
        """
        Create a new task.
        
        Args:
            task_type: Type of task (RecaptchaV2Task, etc.)
            website_url: Target website URL
            website_key: reCAPTCHA site key
            recaptcha_type: normal | invisible | enterprise
            client_key: API key of the client
            **kwargs: Additional task parameters
        
        Returns:
            Created Task object
        """
        task_id = str(uuid.uuid4())
        
        task = Task(
            id=task_id,
            task_type=task_type,
            website_url=website_url,
            website_key=website_key,
            recaptcha_type=recaptcha_type,
            client_key=client_key,
            proxy=kwargs.get('proxy'),
            user_agent=kwargs.get('user_agent'),
            cookies=kwargs.get('cookies'),
            is_invisible=kwargs.get('is_invisible', False),
            page_action=kwargs.get('page_action'),
            enterprise_payload=kwargs.get('enterprise_payload'),
            api_domain=kwargs.get('api_domain'),
        )
        
        with self._lock:
            # Cleanup if at capacity
            if len(self._tasks) >= self._max_tasks:
                self._cleanup_old_tasks()
            
            self._tasks[task_id] = task
            self._total_created += 1
        
        logger.info(f"Created task {task_id} for {website_url}")
        return task
    
    def get_task(self, task_id: str) -> Optional[Task]:
        """Get a task by ID"""
        with self._lock:
            return self._tasks.get(task_id)
    
    def update_task_status(
        self,
        task_id: str,
        status: TaskStatus,
        solution: Optional[Dict] = None,
        error_id: int = 0,
        error_message: Optional[str] = None,
        cost: float = 0.0
    ) -> Optional[Task]:
        """
        Update task status.
        
        Args:
            task_id: Task ID
            status: New status
            solution: Solution data (for READY status)
            error_id: Error code (for FAILED status)
            error_message: Error message
            cost: Cost charged for the task
        
        Returns:
            Updated task or None if not found
        """
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return None
            
            task.status = status
            
            if status == TaskStatus.PROCESSING:
                task.start_time = time.time()
            
            elif status == TaskStatus.READY:
                task.end_time = time.time()
                task.solution = solution
                task.cost = cost
                task.solve_count += 1
                self._total_completed += 1
            
            elif status == TaskStatus.FAILED:
                task.end_time = time.time()
                task.error_id = error_id
                task.error_message = error_message
                self._total_failed += 1
            
            return task
    
    def delete_task(self, task_id: str) -> bool:
        """Delete a task"""
        with self._lock:
            if task_id in self._tasks:
                del self._tasks[task_id]
                return True
            return False
    
    def get_pending_tasks(self, limit: int = 10) -> List[Task]:
        """Get pending tasks for processing"""
        with self._lock:
            pending = [
                task for task in self._tasks.values()
                if task.status == TaskStatus.PENDING
            ]
            return pending[:limit]
    
    def _cleanup_old_tasks(self):
        """Remove expired/old tasks"""
        now = time.time()
        expired_ids = []
        
        for task_id, task in self._tasks.items():
            # Remove completed/failed tasks older than TTL
            if task.status in (TaskStatus.READY, TaskStatus.FAILED, TaskStatus.EXPIRED):
                if task.end_time and (now - task.end_time) > self._task_ttl:
                    expired_ids.append(task_id)
        
        for task_id in expired_ids:
            del self._tasks[task_id]
        
        if expired_ids:
            logger.info(f"Cleaned up {len(expired_ids)} expired tasks")
    
    def get_stats(self) -> Dict[str, Any]:
        """Get task manager statistics"""
        with self._lock:
            status_counts = {}
            for task in self._tasks.values():
                status = task.status.value
                status_counts[status] = status_counts.get(status, 0) + 1
            
            return {
                "total_tasks": len(self._tasks),
                "total_created": self._total_created,
                "total_completed": self._total_completed,
                "total_failed": self._total_failed,
                "status_counts": status_counts,
                "max_tasks": self._max_tasks,
            }
    
    def cleanup(self):
        """Manual cleanup of all old tasks"""
        with self._lock:
            self._cleanup_old_tasks()


# Global task manager instance
_task_manager: Optional[TaskManager] = None


def get_task_manager() -> TaskManager:
    """Get the global task manager instance"""
    global _task_manager
    if _task_manager is None:
        _task_manager = TaskManager()
    return _task_manager
