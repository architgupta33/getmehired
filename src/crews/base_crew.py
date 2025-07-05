"""
Base crew class for managing multiple agents and tasks.
"""
from typing import List, Optional, Dict, Any
from crewai import Crew


class BaseCrew:
    """Base class for all crews in the application."""
    
    def __init__(
        self,
        agents: List,
        tasks: List,
        verbose: bool = True,
        max_iterations: int = 3,
        **kwargs
    ):
        """
        Initialize a base crew.
        
        Args:
            agents: List of agents in the crew
            tasks: List of tasks for the crew
            verbose: Whether to enable verbose output
            max_iterations: Maximum iterations for task execution
            **kwargs: Additional arguments for Crew initialization
        """
        self.agents = agents
        self.tasks = tasks
        self.verbose = verbose
        self.max_iterations = max_iterations
        
        # Extract CrewAI Agent and Task instances
        crew_agents = [
            agent.get_agent() if hasattr(agent, 'get_agent') else agent 
            for agent in agents
        ]
        crew_tasks = [
            task.get_task() if hasattr(task, 'get_task') else task 
            for task in tasks
        ]
        
        # Create the CrewAI Crew instance
        self.crew = Crew(
            agents=crew_agents,
            tasks=crew_tasks,
            verbose=verbose,
            max_iterations=max_iterations,
            **kwargs
        )
    
    def kickoff(self) -> str:
        """
        Start the crew's work.
        
        Returns:
            Crew execution result
        """
        return self.crew.kickoff()
    
    def get_crew(self) -> Crew:
        """Get the underlying CrewAI Crew instance."""
        return self.crew 