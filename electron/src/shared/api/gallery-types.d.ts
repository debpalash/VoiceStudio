export interface GalleryVoice {
  id: string;
  name: string;
  character: string;
  category: string;
  source_type: string;
  source_url?: string;
  audio_path: string;
  duration: number;
  description?: string;
  thumbnail?: string;
  tags: string[];
  is_favorite?: boolean;
  created_at: number;
}

export interface YoutubeSearchResult {
  title: string;
  video_id: string;
  duration: string | null;
  thumbnail: string | null;
}

export interface DownloadParams {
  video_url: string;
  start_time: number;
  duration: number;
  character_name: string;
  category: string;
  description?: string;
}
