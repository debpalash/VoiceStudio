import type { HTMLAttributes, ReactNode } from 'react';
import { cn } from '@/lib/utils';

type SynthesisDockProps = HTMLAttributes<HTMLDivElement> & {
  output: ReactNode;
};

export function SynthesisDock({ output, children, className, ...props }: SynthesisDockProps) {
  return (
    <div className={cn('z-10 mt-auto shrink-0 bg-background', className)} {...props}>
      {output}
      {children}
    </div>
  );
}
