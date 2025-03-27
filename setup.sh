#!/bin/bash

echo "Starting Video Stitcher Setup..."
echo

# Check if Python is installed
if ! command -v python3 &> /dev/null; then
    echo "Python is not installed! Please install Python 3.8 or higher."
    echo "You can download Python from https://www.python.org/downloads/"
    exit 1
fi

# Run the setup script
python3 setup.py
if [ $? -ne 0 ]; then
    echo "Setup failed! Please check the error messages above."
    exit 1
fi

echo
echo "Setup completed successfully!"
echo "You can now run the application using: python3 api/app.py" 