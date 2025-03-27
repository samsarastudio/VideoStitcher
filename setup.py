from setuptools import setup, find_packages
import os
import logging
import shutil

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

def copy_wwe_video():
    """Copy the main WWE video to the uploads folder during installation"""
    try:
        # Get the package directory
        package_dir = os.path.dirname(os.path.abspath(__file__))
        source_video = os.path.join(package_dir, 'wwe_video.mp4')
        target_dir = os.path.join(package_dir, 'api', 'uploads')
        
        # Create uploads directory if it doesn't exist
        os.makedirs(target_dir, exist_ok=True)
        
        # Copy the video file
        target_video = os.path.join(target_dir, 'wwe_video.mp4')
        if os.path.exists(source_video):
            shutil.copy2(source_video, target_video)
            print(f"Successfully copied WWE video to {target_video}")
        else:
            print("Warning: WWE video file not found in package directory")
    except Exception as e:
        print(f"Error copying WWE video: {str(e)}")

def main():
    setup_logging()
    logging.info("Starting setup...")
    create_directories()
    logging.info("Setup completed successfully!")
    copy_wwe_video()

if __name__ == "__main__":
    main()

setup(
    name="video_stitcher_app",
    version="0.1.0",
    packages=find_packages(),
    python_requires=">=3.8",
    install_requires=[
        "flask==2.0.1",
        "gunicorn==20.1.0",
        "numpy==1.23.5",
        "opencv-python-headless==4.7.0.72",
        "moviepy==1.0.3",
        "werkzeug==2.0.1",
        "python-dotenv==0.19.0"
    ],
    entry_points={
        'console_scripts': [
            'video-stitcher=api.app:main',
        ],
    },
    package_data={
        'video_stitcher_app': ['wwe_video.mp4'],
    },
) 