// shadcn-style components kept in source so every class stays editable.
// radius follows importance: controls 6px, panels 12px, badges full

import { cva, type VariantProps } from 'class-variance-authority'
import type {
  ButtonHTMLAttributes,
  InputHTMLAttributes,
  ReactNode,
  TextareaHTMLAttributes,
} from 'react'
import { forwardRef } from 'react'

import { cn } from '@/lib/cn'

// --- button ---------------------------------------------------------------

const buttonVariants = cva(
  // tailwind removes the default outline, keep focus styles for keyboard users
  'inline-flex shrink-0 cursor-pointer items-center justify-center gap-2 rounded-md text-sm ' +
    'font-medium transition-colors duration-150 focus-visible:outline-none ' +
    'focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:ring-offset-2 ' +
    'focus-visible:ring-offset-white active:translate-y-px ' +
    'disabled:pointer-events-none disabled:opacity-45',
  {
    variants: {
      variant: {
        primary: 'bg-brand-600 text-white hover:bg-brand-700',
        secondary: 'bg-slate-100 text-slate-900 hover:bg-slate-200',
        outline: 'border border-slate-300 bg-white text-slate-900 hover:border-slate-400',
        ghost: 'text-slate-700 hover:bg-slate-100',
        danger: 'bg-red-700 text-white hover:bg-red-800',
      },
      size: {
        sm: 'h-8 px-3',
        md: 'h-10 px-4',
        lg: 'h-11 px-5 text-base',
      },
    },
    defaultVariants: { variant: 'primary', size: 'md' },
  },
)

export interface ButtonProps
  extends ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, ...props }, ref) => (
    <button ref={ref} className={cn(buttonVariants({ variant, size }), className)} {...props} />
  ),
)
Button.displayName = 'Button'

// the same look for router links that act as buttons
export function buttonClass({
  variant,
  size,
  className,
}: VariantProps<typeof buttonVariants> & { className?: string }) {
  return cn(buttonVariants({ variant, size }), className)
}

// --- field ----------------------------------------------------------------

export const inputClass =
  'block w-full rounded-md border bg-white px-3 py-2 text-sm text-slate-900 ' +
  'placeholder:text-slate-400 transition-colors focus:border-brand-500 focus:outline-none ' +
  'focus:ring-2 focus:ring-brand-500/25'

export interface FieldProps extends InputHTMLAttributes<HTMLInputElement> {
  label: string
  // shown under the input, linked for screen readers
  error?: string
  hint?: string
}

export const Field = forwardRef<HTMLInputElement, FieldProps>(
  ({ label, error, hint, id, className, ...props }, ref) => {
    const inputId = id ?? props.name ?? label.toLowerCase().replace(/\s+/g, '-')
    const describedBy = error ? `${inputId}-error` : hint ? `${inputId}-hint` : undefined

    return (
      <div className="space-y-1.5">
        <label htmlFor={inputId} className="block text-sm font-medium text-slate-800">
          {label}
        </label>
        <input
          ref={ref}
          id={inputId}
          aria-invalid={error ? true : undefined}
          aria-describedby={describedBy}
          className={cn(inputClass, error ? 'border-red-500' : 'border-slate-300', className)}
          {...props}
        />
        {hint && !error && (
          <p id={`${inputId}-hint`} className="text-xs text-slate-500">
            {hint}
          </p>
        )}
        {error && (
          <p id={`${inputId}-error`} className="text-xs text-red-700">
            {error}
          </p>
        )}
      </div>
    )
  },
)
Field.displayName = 'Field'

export interface TextareaProps extends TextareaHTMLAttributes<HTMLTextAreaElement> {
  label: string
  error?: string
  hint?: string
}

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaProps>(
  ({ label, error, hint, id, className, ...props }, ref) => {
    const inputId = id ?? props.name ?? label.toLowerCase().replace(/\s+/g, '-')
    const describedBy = error ? `${inputId}-error` : hint ? `${inputId}-hint` : undefined

    return (
      <div className="space-y-1.5">
        <label htmlFor={inputId} className="block text-sm font-medium text-slate-800">
          {label}
        </label>
        <textarea
          ref={ref}
          id={inputId}
          aria-invalid={error ? true : undefined}
          aria-describedby={describedBy}
          className={cn(inputClass, error ? 'border-red-500' : 'border-slate-300', className)}
          {...props}
        />
        {hint && !error && (
          <p id={`${inputId}-hint`} className="text-xs text-slate-500">
            {hint}
          </p>
        )}
        {error && (
          <p id={`${inputId}-error`} className="text-xs text-red-700">
            {error}
          </p>
        )}
      </div>
    )
  },
)
Textarea.displayName = 'Textarea'

// --- layout pieces ----------------------------------------------------------

export function PageHeader({
  title,
  description,
  back,
  actions,
}: {
  title: ReactNode
  description?: ReactNode
  back?: ReactNode
  actions?: ReactNode
}) {
  return (
    <header className="mb-8 flex flex-wrap items-end justify-between gap-4">
      <div className="min-w-0">
        {back && <div className="mb-2 text-sm">{back}</div>}
        <h1 className="text-[1.75rem] font-semibold leading-tight tracking-[-0.015em] text-slate-900">
          {title}
        </h1>
        {description && (
          <p className="mt-2 max-w-2xl text-[0.9375rem] leading-relaxed text-slate-600">
            {description}
          </p>
        )}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </header>
  )
}

export function Card({
  title,
  description,
  children,
  className,
  actions,
}: {
  title?: string
  description?: string
  children: ReactNode
  className?: string
  actions?: ReactNode
}) {
  return (
    <section className={cn('rounded-xl border border-slate-200 bg-white p-6', className)}>
      {(title || actions) && (
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            {title && <h2 className="text-base font-semibold text-slate-900">{title}</h2>}
            {description && <p className="mt-1 text-sm text-slate-600">{description}</p>}
          </div>
          {actions}
        </div>
      )}
      {!title && !actions && description && (
        <p className="text-sm text-slate-600">{description}</p>
      )}
      <div className={cn(title || description || actions ? 'mt-5' : undefined)}>{children}</div>
    </section>
  )
}

export function EmptyState({
  icon,
  title,
  children,
  action,
}: {
  icon?: ReactNode
  title: string
  children?: ReactNode
  action?: ReactNode
}) {
  return (
    <div className="flex flex-col items-start gap-3 rounded-xl border border-dashed border-slate-300 bg-white px-6 py-10 sm:items-center sm:text-center">
      {icon && <div className="text-slate-400">{icon}</div>}
      <h3 className="text-base font-semibold text-slate-900">{title}</h3>
      {children && <div className="max-w-md text-sm text-slate-600">{children}</div>}
      {action && <div className="mt-1">{action}</div>}
    </div>
  )
}

// --- alert, badge, spinner --------------------------------------------------

const alertVariants = cva('rounded-lg border-l-4 px-4 py-3 text-sm', {
  variants: {
    tone: {
      error: 'border-red-600 bg-red-50 text-red-900',
      warning: 'border-amber-500 bg-amber-50 text-amber-950',
      success: 'border-brand-600 bg-brand-50 text-brand-800',
      info: 'border-slate-400 bg-slate-100 text-slate-800',
    },
  },
  defaultVariants: { tone: 'info' },
})

export function Alert({
  tone,
  children,
}: VariantProps<typeof alertVariants> & { children: ReactNode }) {
  // role=alert so screen readers announce errors after a failed submit
  return (
    <div role={tone === 'error' ? 'alert' : 'status'} className={alertVariants({ tone })}>
      {children}
    </div>
  )
}

const badgeVariants = cva(
  'inline-flex shrink-0 items-center gap-1 whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium',
  {
    variants: {
      tone: {
        neutral: 'bg-slate-100 text-slate-700',
        info: 'bg-brand-50 text-brand-800',
        success: 'bg-brand-100 text-brand-800',
        warning: 'bg-amber-100 text-amber-900',
        error: 'bg-red-100 text-red-800',
        marker: 'bg-marker text-slate-900',
      },
    },
    defaultVariants: { tone: 'neutral' },
  },
)

export function Badge({
  tone,
  children,
}: VariantProps<typeof badgeVariants> & { children: ReactNode }) {
  return <span className={badgeVariants({ tone })}>{children}</span>
}

export function Spinner({ label = 'Loading' }: { label?: string }) {
  return (
    <span className="inline-flex items-center gap-2 text-sm text-slate-600" role="status">
      <span
        aria-hidden="true"
        className="h-4 w-4 animate-spin rounded-full border-2 border-slate-300 border-t-brand-600"
      />
      {label}
    </span>
  )
}

export function ProgressBar({ percent }: { percent: number }) {
  return (
    <div
      className="h-1.5 overflow-hidden rounded-full bg-slate-200"
      role="progressbar"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(percent)}
    >
      <div
        className="h-full rounded-full bg-brand-600 transition-[width] duration-500"
        style={{ width: `${Math.max(percent, 4)}%` }}
      />
    </div>
  )
}

export function StatusDot({ ok }: { ok: boolean }) {
  return (
    <span
      aria-hidden="true"
      className={cn('inline-block h-2.5 w-2.5 rounded-full', ok ? 'bg-brand-500' : 'bg-red-600')}
    />
  )
}
