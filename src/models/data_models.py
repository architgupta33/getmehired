"""
Data models for the CrewAI application.
"""
from typing import Optional, List, Dict, Any
from datetime import datetime
from pydantic import BaseModel, Field


class AgentConfig(BaseModel):
    """Configuration model for agents."""
    name: str = Field(..., description="Agent name")
    role: str = Field(..., description="Agent role")
    goal: str = Field(..., description="Agent goal")
    backstory: str = Field(..., description="Agent backstory")
    verbose: bool = Field(default=True, description="Enable verbose output")
    allow_delegation: bool = Field(default=False, description="Allow task delegation")


class TaskConfig(BaseModel):
    """Configuration model for tasks."""
    description: str = Field(..., description="Task description")
    expected_output: str = Field(default="", description="Expected output format")
    context: Optional[Dict[str, Any]] = Field(default=None, description="Task context")


class CrewConfig(BaseModel):
    """Configuration model for crews."""
    name: str = Field(..., description="Crew name")
    agents: List[str] = Field(..., description="List of agent names")
    tasks: List[str] = Field(..., description="List of task descriptions")
    verbose: bool = Field(default=True, description="Enable verbose output")
    max_iterations: int = Field(default=3, description="Maximum iterations")


class ExecutionResult(BaseModel):
    """Model for execution results."""
    success: bool = Field(..., description="Whether execution was successful")
    result: str = Field(..., description="Execution result")
    timestamp: datetime = Field(default_factory=datetime.now, description="Execution timestamp")
    metadata: Optional[Dict[str, Any]] = Field(default=None, description="Additional metadata")


class LogEntry(BaseModel):
    """Model for log entries."""
    level: str = Field(..., description="Log level")
    message: str = Field(..., description="Log message")
    timestamp: datetime = Field(default_factory=datetime.now, description="Log timestamp")
    module: Optional[str] = Field(default=None, description="Module name")
    function: Optional[str] = Field(default=None, description="Function name") 