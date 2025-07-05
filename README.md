# getmehired
App to help job seekers reach more people for roles/companies

# CrewAI Agentic Application

A well-structured Python application for building agentic AI systems using CrewAI framework.

## 🚀 Features

- **Modular Architecture**: Clean separation of agents, tasks, crews, and tools
- **Configuration Management**: Environment-based settings with Pydantic validation
- **Logging System**: Comprehensive logging with file and console output
- **Testing Framework**: Unit and integration test structure
- **Documentation**: Well-documented code with examples

## 📁 Project Structure

```
getmehired/
├── src/
│   ├── agents/          # Agent definitions and implementations
│   ├── tasks/           # Task definitions and implementations
│   ├── tools/           # Custom tools for agents
│   ├── crews/           # Crew configurations and management
│   ├── models/          # Data models and schemas
│   ├── utils/           # Utility functions and helpers
│   └── config/          # Configuration management
├── tests/
│   ├── unit/            # Unit tests
│   └── integration/     # Integration tests
├── docs/                # Documentation
├── examples/            # Example implementations
├── data/
│   ├── input/           # Input data files
│   └── output/          # Output data files
├── logs/                # Application logs
├── main.py              # Main application entry point
├── requirements.txt     # Python dependencies
└── env.example          # Environment variables template
```

## 🛠️ Installation

1. **Clone the repository** (if applicable)
2. **Activate your virtual environment**:
   ```bash
   source venv/bin/activate  # On macOS/Linux
   # or
   venv\Scripts\activate     # On Windows
   ```

3. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

4. **Set up environment variables**:
   ```bash
   cp env.example .env
   # Edit .env and add your OpenAI API key
   ```

## 🔧 Configuration

Create a `.env` file with the following variables:

```env
# OpenAI Configuration
OPENAI_API_KEY=your_openai_api_key_here

# Application Configuration
DEBUG=false
LOG_LEVEL=INFO

# CrewAI Configuration
CREWAI_VERBOSE=true
CREWAI_MAX_ITERATIONS=3
```

## 🚀 Quick Start

1. **Run the basic example**:
   ```bash
   python examples/basic_example.py
   ```

2. **Run the main application**:
   ```bash
   python main.py
   ```

## 📚 Usage

### Creating Agents

```python
from src.agents.base_agent import BaseAgent

class ResearchAgent(BaseAgent):
    def __init__(self):
        super().__init__(
            name="Research Agent",
            role="Research Specialist",
            goal="Conduct thorough research on given topics",
            backstory="Expert researcher with years of experience",
            verbose=True
        )
```

### Creating Tasks

```python
from src.tasks.base_task import BaseTask

class ResearchTask(BaseTask):
    def __init__(self, agent):
        super().__init__(
            description="Research AI in healthcare",
            agent=agent,
            expected_output="Detailed research report"
        )
```

### Creating Crews

```python
from src.crews.base_crew import BaseCrew

class ResearchCrew(BaseCrew):
    def __init__(self, agents, tasks):
        super().__init__(
            agents=agents,
            tasks=tasks,
            verbose=True,
            max_iterations=3
        )
```

## 🧪 Testing

Run tests with pytest:

```bash
# Run all tests
pytest

# Run unit tests only
pytest tests/unit/

# Run integration tests only
pytest tests/integration/
```

## 📝 Development

### Code Style

This project uses:
- **Black** for code formatting
- **Flake8** for linting
- **MyPy** for type checking

Format your code:
```bash
black src/ tests/
```

### Adding New Features

1. Create your agent in `src/agents/`
2. Create your task in `src/tasks/`
3. Create your crew in `src/crews/`
4. Add tests in `tests/`
5. Update documentation

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests
5. Submit a pull request

## 📄 License

This project is licensed under the MIT License.

## 🆘 Support

For support and questions, please open an issue in the repository.
