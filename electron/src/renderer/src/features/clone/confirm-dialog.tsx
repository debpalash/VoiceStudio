import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';
import { rendererTask } from '@/lib/global-error-recovery';
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';

interface ConfirmDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description: string;
  confirmLabel?: string;
  cancelLabel?: string;
  destructive?: boolean;
  onConfirm: () => void | Promise<void>;
  onCancel?: () => void | Promise<void>;
}

export function ConfirmDialog({
  open,
  onOpenChange,
  title,
  description,
  confirmLabel,
  cancelLabel,
  destructive = true,
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  const { t } = useTranslation();
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent showCloseButton={false}>
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <DialogClose
            render={<Button variant="outline" onClick={() => rendererTask(() => onCancel?.())} />}
          >
            {cancelLabel ?? t('common.cancel')}
          </DialogClose>
          <Button
            variant={destructive ? 'destructive' : 'default'}
            onClick={() =>
              rendererTask(async () => {
                await onConfirm();
                onOpenChange(false);
              })
            }
          >
            {confirmLabel ?? t('common.confirm')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
