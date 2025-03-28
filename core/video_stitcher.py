import cv2
import numpy as np
from moviepy.editor import VideoFileClip, AudioFileClip, concatenate_videoclips, CompositeVideoClip
import os

class VideoStitcher:
    def __init__(self, wwe_video_path, fan_video_path):
        """
        Initialize the VideoStitcher with paths to input videos
        """
        try:
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
            
            # Set transition duration
            self.transition_duration = 0.5  # seconds
            
            # Print video information for debugging
            print(f"WWE Video FPS: {self.wwe_fps}")
            print(f"Fan Video FPS: {self.fan_fps}")
            print(f"Target FPS: {self.target_fps}")
            print(f"WWE Video Duration: {self.wwe_video.duration:.2f} seconds")
            print(f"Fan Video Duration: {self.fan_video.duration:.2f} seconds")
            
        except Exception as e:
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
            
            if progress_callback:
                progress_callback(1.0, 'completed')
            
            return True, "Video stitching completed successfully"
            
        except Exception as e:
            if progress_callback:
                progress_callback(0.0, 'failed')
            return False, f"Error during video stitching: {str(e)}" 