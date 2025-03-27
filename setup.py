from setuptools import setup, find_packages
import os
import logging

def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )

def create_directories():
    """Create necessary directories for the application"""
    # Use Render's temporary directory
    render_temp_dir = os.getenv('RENDER_TEMP_DIR', '/tmp')
    directories = [
        os.path.join(render_temp_dir, 'uploads'),
        os.path.join(render_temp_dir, 'outputs')
    ]
    
    for directory in directories:
        os.makedirs(directory, exist_ok=True)
        logging.info(f"Created directory: {directory}")

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
    python_requires='>=3.8,<3.9',
    install_requires=[
        'flask==2.0.1',
        'gunicorn==20.1.0',
        'opencv-python==4.5.3.56',
        'numpy==1.21.2',
        'werkzeug==2.0.1',
        'python-dotenv==0.19.0',
        'moviepy==1.0.3'
    ],
    entry_points={
        'console_scripts': [
            'video-stitcher-setup=setup:main',
        ],
    },
) 