from setuptools import setup, find_packages
import os
import sys
import subprocess
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

def install_requirements():
    """Install required packages using pip"""
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"])
        logging.info("Successfully installed required packages")
    except subprocess.CalledProcessError as e:
        logging.error(f"Failed to install requirements: {str(e)}")
        sys.exit(1)

def create_directories():
    """Create necessary directories for the application"""
    directories = [
        'static/uploads',
        'static/outputs',
        'api/templates',
        'core'
    ]
    
    for directory in directories:
        try:
            os.makedirs(directory, exist_ok=True)
            logging.info(f"Created directory: {directory}")
        except Exception as e:
            logging.error(f"Failed to create directory {directory}: {str(e)}")
            sys.exit(1)

def setup_application():
    """Main setup function"""
    logging.info("Starting application setup...")
    
    # Install requirements
    install_requirements()
    
    # Create directories
    create_directories()
    
    logging.info("Setup completed successfully!")

if __name__ == "__main__":
    setup_application() 