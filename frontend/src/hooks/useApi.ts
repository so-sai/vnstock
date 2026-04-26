import { useQuery } from '@tanstack/react-query';
import { api } from '../lib/api';

export function useMacro() {
  return useQuery({
    queryKey: ['macro'],
    queryFn: api.getMacro,
  });
}

export function useScreener() {
  return useQuery({
    queryKey: ['screener'],
    queryFn: api.getScreener,
  });
}

export function useDashboard() {
  return useQuery({
    queryKey: ['dashboard'],
    queryFn: api.getDashboard,
  });
}
