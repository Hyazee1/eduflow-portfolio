import { useQuery } from '@tanstack/react-query';
import { get } from '../api/client';

export function useBootstrap() {
  return useQuery({ queryKey: ['bootstrap'], queryFn: () => get('/bootstrap'), staleTime: 30000 });
}
