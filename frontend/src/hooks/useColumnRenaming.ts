import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../api/endpoints';

export function columnRenamingKey(runId: string | undefined) {
  return ['column-renaming', runId] as const;
}

/** Suggested renamings + revised discrepancies. Separate from the discrepancy query on purpose. */
export function useColumnRenaming(runId: string | undefined, enabled: boolean) {
  const queryClient = useQueryClient();
  const query = useQuery({
    queryKey: columnRenamingKey(runId),
    queryFn: () => api.columnRenaming.get(runId as string),
    enabled: !!runId && enabled,
    // A missing/failed suggestion lookup must never get in the way of the review itself.
    retry: false,
  });
  const onSuccess = (workspace: Awaited<ReturnType<typeof api.columnRenaming.get>>) =>
    queryClient.setQueryData(columnRenamingKey(runId), workspace);

  const accept = useMutation({
    mutationFn: ({ keys, note }: { keys: string[]; note?: string }) =>
      api.columnRenaming.accept(runId as string, { keys, note }),
    onSuccess,
  });
  const revert = useMutation({
    mutationFn: (key: string) => api.columnRenaming.revert(runId as string, key),
    onSuccess,
  });
  return { query, accept, revert };
}
