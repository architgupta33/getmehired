"""
Simple CrewAI example using Groq API.
"""
import os
from dotenv import load_dotenv
from crewai import Agent, Task, Crew, LLM
from src.utils.logger import logger


def create_groq_crew():
    """Create a simple crew using Groq."""
    
    # Use Groq API with CrewAI LLM class
    llm = LLM(model="groq/llama3-8b-8192")
    
    # Create a research agent
    agent = Agent(
        role="Research Specialist",
        goal="Conduct research and provide detailed analysis",
        backstory="You are an expert researcher with years of experience in gathering and analyzing information.",
        verbose=True,
        allow_delegation=False,
        llm=llm
    )
    
    # Create a research task
    task = Task(
        description="Say hello.",
        agent=agent,
        expected_output="A simple hello message."
    )
    
    # Create the crew
    crew = Crew(
        agents=[agent],
        tasks=[task],
        verbose=True
    )
    
    return crew


def main():
    """Run the Groq example."""
    # Load environment variables
    load_dotenv()
    
    # Print masked Groq API key for debugging
    groq_key = os.getenv("GROQ_API_KEY", "")
    if groq_key:
        print(f"Loaded GROQ_API_KEY: {groq_key[:4]}...{groq_key[-4:]}")
    else:
        print("GROQ_API_KEY not found in environment!")
    
    logger.info("Starting Groq CrewAI example...")
    
    try:
        # Create and run the crew
        crew = create_groq_crew()
        result = crew.kickoff()
        
        print("\n" + "="*50)
        print("GROQ CREWAI RESULTS")
        print("="*50)
        print(result)
        print("="*50)
        
        logger.info("Groq example completed successfully!")
        
    except Exception as e:
        logger.error(f"Example failed: {str(e)}")
        print(f"\nError: {str(e)}")
        print("\nThis might be due to:")
        print("1. Groq API key not set correctly")
        print("2. Network connectivity issues")
        print("3. Model availability issues")
        print("\nTry:")
        print("- Check your API key in .env file")
        print("- Verify your Groq account status")


if __name__ == "__main__":
    main() 