import cv2
import numpy as np
from moviepy.editor import VideoFileClip, AudioFileClip, concatenate_videoclips, CompositeVideoClip
import os

class VideoStitcher:
    def __init__(self, wwe_video_path, fan_video_path):
        """
        Initialize the VideoStitcher with paths to input videos
        """
        self.wwe_video = VideoFileClip(wwe_video_path)
        self.fan_video = VideoFileClip(fan_video_path)
        
        # Extract audio from fan video
        self.commentary = self.fan_video.audio
        
        # Get frame rates
        self.wwe_fps = self.wwe_video.fps
        self.fan_fps = self.fan_video.fps
        
        # Use the higher frame rate for the final video
        self.target_fps = max(self.wwe_fps, self.fan_fps)
        
        # Ensure all videos are the same size and frame rate
        self.target_size = (1920, 1080)  # Standard HD resolution
        self.wwe_video = self.wwe_video.resize(self.target_size).set_fps(self.target_fps)
        self.fan_video = self.fan_video.resize(self.target_size).set_fps(self.target_fps)
        
        # Set the duration of the final video
        self.final_duration = 30
        
        # Set transition duration
        self.transition_duration = 0.5  # seconds

    def create_video_segments(self):
        """
        Create video segments according to the specified timing with fade transitions
        """
        segments = []
        
        # Calculate the duration of each segment
        wwe_duration = self.wwe_video.duration
        fan_duration = self.fan_video.duration
        
        # Define segment timings
        segment_timings = [
            (0, 4, "wwe"),    # 0s - 4s: Intro (WWE video)
            (4, 7, "fan"),    # 4s - 7s: Fan video
            (7, 10, "wwe"),   # 7s - 10s: WWE video
            (10, 13, "fan"),  # 10s - 13s: Fan video
            (13, 22, "wwe"),  # 13s - 22s: WWE video
            (22, 25, "fan"),  # 22s - 25s: Fan video
            (25, 30, "wwe")   # 25s - 30s: Final WWE video
        ]
        
        # Create segments based on timing
        for i, (start_time, end_time, video_type) in enumerate(segment_timings):
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
        
        return segments

    def stitch_videos(self, output_path):
        """
        Stitch all video segments together with commentary audio
        """
        try:
            # Create video segments
            segments = self.create_video_segments()
            
            # Concatenate all video segments
            final_video = concatenate_videoclips(segments)
            
            # Ensure final video has correct frame rate
            final_video = final_video.set_fps(self.target_fps)
            
            # Set the commentary audio for the entire duration
            final_video = final_video.set_audio(self.commentary)
            
            # Write the final video
            final_video.write_videofile(
                output_path,
                codec='libx264',
                audio_codec='aac',
                temp_audiofile='temp-audio.m4a',
                remove_temp=True,
                fps=self.target_fps
            )
            
            # Close all clips to free up resources
            final_video.close()
            self.wwe_video.close()
            self.fan_video.close()
            self.commentary.close()
            
            return True, "Video stitching completed successfully"
            
        except Exception as e:
            return False, f"Error during video stitching: {str(e)}" 