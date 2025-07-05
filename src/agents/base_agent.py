"""
Base agent class for CrewAI agents.
"""
from typing import Optional, List, Dict, Any
from crewai import Agent


class BaseAgent:
    """Base class for all agents in the application."""
    
    def __init__(
        self,
        name: str,
        role: str,
        goal: str,
        backstory: str,
        verbose: bool = True,
        allow_delegation: bool = False,
        tools: Optional[List] = None,
        **kwargs
    ):
        """
        Initialize a base agent.
        
        Args:
            name: Agent name
            role: Agent role description
            goal: Agent's primary goal
            backstory: Agent's background story
            verbose: Whether to enable verbose output
            allow_delegation: Whether agent can delegate tasks
            tools: List of tools available to the agent
            **kwargs: Additional arguments for Agent initialization
        """
        self.name = name
        self.role = role
        self.goal = goal
        self.backstory = backstory
        self.verbose = verbose
        self.allow_delegation = allow_delegation
        self.tools = tools or []
        
        # Create the CrewAI Agent instance
        self.agent = Agent(
            name=name,
            role=role,
            goal=goal,
            backstory=backstory,
            verbose=verbose,
            allow_delegation=allow_delegation,
            tools=self.tools,
            **kwargs
        )
    
    def execute_task(self, task_description: str) -> str:
        """
        Execute a task using this agent.
        
        Args:
            task_description: Description of the task to execute
            
        Returns:
            Task execution result
        """
        # This would be implemented by subclasses or used with CrewAI tasks
        pass
    
    def get_agent(self) -> Agent:
        """Get the underlying CrewAI Agent instance."""
        return self.agent 