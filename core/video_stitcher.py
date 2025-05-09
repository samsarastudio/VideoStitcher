import cv2
import numpy as np
from moviepy.editor import VideoFileClip, AudioFileClip, concatenate_videoclips, CompositeVideoClip
import os
import subprocess
import logging

def check_ffmpeg_version():
    """Check FFMPEG version and return True if it's recent enough"""
    try:
        result = subprocess.run(['ffmpeg', '-version'], capture_output=True, text=True)
        version_line = result.stdout.split('\n')[0]
        version = version_line.split(' ')[2].split('.')[0]  # Get major version number
        return int(version) >= 4
    except Exception as e:
        logging.error(f"Error checking FFMPEG version: {str(e)}")
        return False

def validate_video_file(file_path):
    """Validate a video file using FFMPEG"""
    try:
        # Check if file exists
        if not os.path.exists(file_path):
            return False, "File does not exist"
        
        # Check file size
        size_mb = os.path.getsize(file_path) / (1024 * 1024)
        if size_mb < 0.01:  # Less than 10KB
            return False, f"File too small: {size_mb:.2f} MB"
        
        # Use FFMPEG to check video file
        result = subprocess.run([
            'ffmpeg', '-v', 'error', '-i', file_path,
            '-f', 'null', '-'
        ], capture_output=True, text=True)
        
        if result.stderr:
            return False, f"Invalid video file: {result.stderr}"
        
        return True, "Video file is valid"
    except Exception as e:
        return False, f"Error validating video: {str(e)}"

class VideoStitcher:
    def __init__(self, wwe_video_path, fan_video_path):
        """
        Initialize the VideoStitcher with paths to input videos
        """
        try:
            # Check FFMPEG version first
            if not check_ffmpeg_version():
                raise Exception("FFMPEG version is too old. Please update to version 4 or higher.")
            
            # Validate both video files
            for video_path in [wwe_video_path, fan_video_path]:
                is_valid, message = validate_video_file(video_path)
                if not is_valid:
                    raise Exception(f"Invalid video file {os.path.basename(video_path)}: {message}")
            
            # Try to load videos with error handling
            try:
                self.wwe_video = VideoFileClip(wwe_video_path)
                self.fan_video = VideoFileClip(fan_video_path)
            except Exception as e:
                raise Exception(f"Failed to load video files: {str(e)}")
            
            # Extract audio from fan video
            try:
                self.commentary = self.fan_video.audio
            except Exception as e:
                logging.warning(f"Could not extract audio from fan video: {str(e)}")
                self.commentary = None
            
            # Get frame rates
            self.wwe_fps = self.wwe_video.fps
            self.fan_fps = self.fan_video.fps
            
            if self.wwe_fps <= 0 or self.fan_fps <= 0:
                raise Exception("Invalid frame rate detected in one or both videos")
            
            # Use the higher frame rate for the final video
            self.target_fps = max(self.wwe_fps, self.fan_fps)
            
            # Ensure all videos are the same size and frame rate
            self.target_size = (1920, 1080)  # Standard HD resolution
            self.wwe_video = self.wwe_video.resize(self.target_size).set_fps(self.target_fps)
            self.fan_video = self.fan_video.resize(self.target_size).set_fps(self.target_fps)
            
            # Set transition duration
            self.transition_duration = 0.5  # seconds
            
            # Print video information for debugging
            logging.info(f"WWE Video FPS: {self.wwe_fps}")
            logging.info(f"Fan Video FPS: {self.fan_fps}")
            logging.info(f"Target FPS: {self.target_fps}")
            logging.info(f"WWE Video Duration: {self.wwe_video.duration:.2f} seconds")
            logging.info(f"Fan Video Duration: {self.fan_video.duration:.2f} seconds")
            
        except Exception as e:
            # Clean up any opened resources
            try:
                if hasattr(self, 'wwe_video'):
                    self.wwe_video.close()
                if hasattr(self, 'fan_video'):
                    self.fan_video.close()
                if hasattr(self, 'commentary') and self.commentary:
                    self.commentary.close()
            except:
                pass
            raise Exception(f"Error initializing VideoStitcher: {str(e)}")

    def create_video_segments(self, progress_callback=None):
        """
        Create video segments according to the specified timing with fade transitions
        """
        try:
            segments = []
            
            # Calculate the duration of each segment
            wwe_duration = self.wwe_video.duration
            fan_duration = self.fan_video.duration
            
            # Calculate segment durations based on available video lengths
            total_duration = min(wwe_duration, fan_duration)
            segment_duration = total_duration / 7  # Split into 7 segments
            
            # Define segment timings
            segment_timings = []
            for i in range(7):
                start_time = i * segment_duration
                end_time = (i + 1) * segment_duration
                video_type = "wwe" if i % 2 == 0 else "fan"
                segment_timings.append((start_time, end_time, video_type))
            
            total_segments = len(segment_timings)
            
            # Create segments based on timing
            for i, (start_time, end_time, video_type) in enumerate(segment_timings):
                if progress_callback:
                    progress = 0.1 + (i / total_segments) * 0.3  # 10% to 40% progress
                    progress_callback(progress, 'processing')
                
                try:
                    if video_type == "wwe":
                        segment = self.wwe_video.subclip(start_time, min(end_time, wwe_duration))
                    else:  # fan video
                        segment = self.fan_video.subclip(start_time, min(end_time, fan_duration))
                    
                    # Add fade in to all segments except the first one
                    if i > 0:
                        segment = segment.fadein(self.transition_duration)
                    
                    # Add fade out to all segments except the last one
                    if i < len(segment_timings) - 1:
                        segment = segment.fadeout(self.transition_duration)
                    
                    segments.append(segment)
                except Exception as e:
                    raise Exception(f"Error creating segment {i}: {str(e)}")
            
            # Print segment durations for debugging
            print("\nSegment durations:")
            for i, segment in enumerate(segments):
                print(f"Segment {i}: {segment.duration:.2f} seconds (FPS: {segment.fps})")
            
            return segments
            
        except Exception as e:
            raise Exception(f"Error in create_video_segments: {str(e)}")

    def stitch_videos(self, output_path, progress_callback=None):
        """
        Stitch all video segments together with commentary audio
        """
        try:
            if progress_callback:
                progress_callback(0.0, 'initializing')
            
            # Create video segments
            segments = self.create_video_segments(progress_callback)
            
            if progress_callback:
                progress_callback(0.4, 'merging')
            
            # Concatenate all video segments
            final_video = concatenate_videoclips(segments)
            
            # Ensure final video has correct frame rate
            final_video = final_video.set_fps(self.target_fps)
            
            if progress_callback:
                progress_callback(0.6, 'adding_audio')
            
            # Set the commentary audio for the entire duration
            final_video = final_video.set_audio(self.commentary)
            
            if progress_callback:
                progress_callback(0.8, 'finalizing')
            
            # Write the final video with timeout handling
            try:
                final_video.write_videofile(
                    output_path,
                    codec='libx264',
                    audio_codec='aac',
                    temp_audiofile='temp-audio.m4a',
                    remove_temp=True,
                    fps=self.target_fps,
                    threads=2,  # Use multiple threads for encoding
                    preset='medium',  # Balance between speed and quality
                    bitrate='5000k'  # Set a reasonable bitrate
                )
            except Exception as e:
                if "timeout" in str(e).lower():
                    raise Exception("Video processing timed out. Please try again with a shorter video.")
                raise
            
            # Close all clips to free up resources
            final_video.close()
            self.wwe_video.close()
            self.fan_video.close()
            self.commentary.close()
            
            if progress_callback:
                progress_callback(1.0, 'completed')
            
            return True, "Video stitching completed successfully"
            
        except Exception as e:
            if progress_callback:
                progress_callback(0.0, 'failed')
            return False, f"Error during video stitching: {str(e)}" 