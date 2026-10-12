import { type ClassValue, clsx } from 'clsx'
import { twMerge } from 'tailwind-merge'

// twMerge drops conflicting tailwind classes so className props override defaults
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs))
}
