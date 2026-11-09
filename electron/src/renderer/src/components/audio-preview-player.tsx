import type { ComponentProps } from 'react';
import { StudioMediaPlayer, audioSource, audioLoaders, MediaProvider } from './media-player';

type PlayerProps = ComponentProps<typeof StudioMediaPlayer>;

/**
 * The hidden player behind `AudioPreviewButton`. Its own module so the button
 * (shown in lists and the setup screens) does not load Vidstack until a
 * preview is first played.
 */
export default function AudioPreviewPlayer({
  src,
  ...props
}: Omit<PlayerProps, 'src' | 'children' | 'viewType' | 'load' | 'autoPlay' | 'className'> & {
  src: string;
}) {
  return (
    <StudioMediaPlayer
      {...props}
      src={audioSource(src)}
      viewType="audio"
      load="eager"
      autoPlay
      className="hidden"
    >
      <MediaProvider loaders={audioLoaders} className="hidden" />
    </StudioMediaPlayer>
  );
}
