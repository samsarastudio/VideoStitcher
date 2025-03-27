from setuptools import setup, find_packages
import os
import sys
import logging

def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler('setup.log'),
            logging.StreamHandler()
        ]
    )

def create_directories():
    """Create necessary directories for the application"""
    base_dir = os.path.dirname(os.path.abspath(__file__))
    directories = [
        os.path.join(base_dir, 'static', 'uploads'),
        os.path.join(base_dir, 'static', 'outputs'),
        os.path.join(base_dir, 'api', 'templates'),
        os.path.join(base_dir, 'core')
    ]
    
    for directory in directories:
        try:
            os.makedirs(directory, exist_ok=True)
            logging.info(f"Created directory: {directory}")
        except Exception as e:
            logging.error(f"Failed to create directory {directory}: {str(e)}")
            sys.exit(1)

def main():
    setup_logging()
    logging.info("Starting setup...")
    create_directories()
    logging.info("Setup completed successfully!")

if __name__ == "__main__":
    main()

setup(
    name="video_stitcher",
    version="1.0.0",
    packages=find_packages(),
    install_requires=[
        'flask==2.0.1',
        'gunicorn==20.1.0',
        'opencv-python==4.5.3.56',
        'numpy==1.21.2',
        'werkzeug==2.0.1',
        'python-dotenv==0.19.0'
    ],
    entry_points={
        'console_scripts': [
            'video-stitcher-setup=setup:main',
        ],
    },
) 