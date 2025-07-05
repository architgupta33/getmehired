"""
Simple CrewAI example using OpenAI API.
"""
import os
from dotenv import load_dotenv
from crewai import Agent, Task, Crew
from langchain_openai import ChatOpenAI
from src.utils.logger import logger


def create_openai_crew():
    """Create a simple crew using OpenAI."""
    
    # Use OpenAI API
    llm = ChatOpenAI(
        model="gpt-3.5-turbo",
        api_key=os.getenv("OPENAI_API_KEY"),
        temperature=0.7
    )
    
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
        description="Research the benefits of using CrewAI for building agentic applications. Provide a brief overview of what CrewAI is and its main advantages.",
        agent=agent,
        expected_output="A brief overview of CrewAI and its benefits for building agentic applications."
    )
    
    # Create the crew
    crew = Crew(
        agents=[agent],
        tasks=[task],
        verbose=True
    )
    
    return crew


def main():
    """Run the OpenAI example."""
    # Load environment variables
    load_dotenv()
    
    # Check for OpenAI API key
    openai_key = os.getenv("OPENAI_API_KEY")
    if not openai_key or openai_key == "your_openai_api_key_here":
        logger.error("OPENAI_API_KEY not found or still has placeholder value.")
        print("\nTo get an OpenAI API key:")
        print("1. Go to https://platform.openai.com/api-keys")
        print("2. Create a new API key")
        print("3. Edit your .env file and replace 'your_openai_api_key_here' with your actual key")
        return
    
    logger.info("Starting OpenAI CrewAI example...")
    
    try:
        # Create and run the crew
        crew = create_openai_crew()
        result = crew.kickoff()
        
        print("\n" + "="*50)
        print("OPENAI CREWAI RESULTS")
        print("="*50)
        print(result)
        print("="*50)
        
        logger.info("OpenAI example completed successfully!")
        
    except Exception as e:
        logger.error(f"Example failed: {str(e)}")
        print(f"\nError: {str(e)}")
        print("\nThis might be due to:")
        print("1. OpenAI API key not set correctly")
        print("2. Quota exceeded - check your billing")
        print("3. Network connectivity issues")
        print("\nTo resolve quota issues:")
        print("- Check your OpenAI billing: https://platform.openai.com/account/billing")
        print("- Add payment method if needed")
        print("- Wait for quota reset if you've hit limits")


if __name__ == "__main__":
    main() 