"""
Simple CrewAI example using Hugging Face free API.
"""
import os
from dotenv import load_dotenv
from crewai import Agent, Task, Crew
from langchain_openai import ChatOpenAI
from src.utils.logger import logger


def create_huggingface_crew():
    """Create a simple crew using Hugging Face."""
    
    # Use Hugging Face's free inference API
    llm = ChatOpenAI(
        model="microsoft/DialoGPT-medium",
        base_url="https://api-inference.huggingface.co/models/",
        api_key=os.getenv("HUGGINGFACE_API_KEY"),
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
    """Run the Hugging Face example."""
    # Load environment variables
    load_dotenv()
    
    # Check for Hugging Face API key
    huggingface_key = os.getenv("HUGGINGFACE_API_KEY")
    if not huggingface_key or huggingface_key == "your_huggingface_token_here":
        logger.error("HUGGINGFACE_API_KEY not found or still has placeholder value.")
        print("\nTo get a free Hugging Face API key:")
        print("1. Go to https://huggingface.co/settings/tokens")
        print("2. Create a new token (it's free)")
        print("3. Edit your .env file and replace 'your_huggingface_token_here' with your actual token")
        return
    
    logger.info("Starting Hugging Face CrewAI example...")
    
    try:
        # Create and run the crew
        crew = create_huggingface_crew()
        result = crew.kickoff()
        
        print("\n" + "="*50)
        print("HUGGING FACE CREWAI RESULTS")
        print("="*50)
        print(result)
        print("="*50)
        
        logger.info("Hugging Face example completed successfully!")
        
    except Exception as e:
        logger.error(f"Example failed: {str(e)}")
        print(f"\nError: {str(e)}")
        print("\nThis might be due to:")
        print("1. Hugging Face API key not set correctly")
        print("2. Model not available or overloaded")
        print("3. Free tier rate limits")
        print("\nTry:")
        print("- Check your API key in .env file")
        print("- Wait a few minutes and try again")


if __name__ == "__main__":
    main() 