"""
Base task class for CrewAI tasks.
"""
from typing import Optional, List, Dict, Any
from crewai import Task


class BaseTask:
    """Base class for all tasks in the application."""
    
    def __init__(
        self,
        description: str,
        agent,
        expected_output: str = "",
        context: Optional[Dict[str, Any]] = None,
        **kwargs
    ):
        """
        Initialize a base task.
        
        Args:
            description: Task description
            agent: Agent to execute the task
            expected_output: Expected output format
            context: Additional context for the task
            **kwargs: Additional arguments for Task initialization
        """
        self.description = description
        self.agent = agent
        self.expected_output = expected_output
        self.context = context or {}
        
        # Create the CrewAI Task instance
        self.task = Task(
            description=description,
            agent=agent.get_agent() if hasattr(agent, 'get_agent') else agent,
            expected_output=expected_output,
            context=self.context,
            **kwargs
        )
    
    def get_task(self) -> Task:
        """Get the underlying CrewAI Task instance."""
        return self.task 