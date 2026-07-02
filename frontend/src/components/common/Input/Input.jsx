import { forwardRef } from 'react';
import { cn } from '@utils/helpers';
import styles from './Input.module.scss';

const Input = forwardRef(function Input(
  {
    label,
    id,
    error,
    hint,
    required,
    className,
    wrapperClassName,
    ...rest
  },
  ref,
) {
  const inputId = id || label?.toLowerCase().replace(/\s+/g, '-');

  return (
    <div className={cn(styles.field, wrapperClassName)}>
      {label && (
        <label htmlFor={inputId} className={styles.field__label}>
          {label}
          {required && <span className={styles.field__required} aria-hidden="true">*</span>}
        </label>
      )}
      <input
        ref={ref}
        id={inputId}
        className={cn(styles.field__input, error && styles['field__input--error'], className)}
        aria-invalid={Boolean(error)}
        aria-describedby={error ? `${inputId}-error` : hint ? `${inputId}-hint` : undefined}
        {...rest}
      />
      {hint && !error && (
        <p id={`${inputId}-hint`} className={styles.field__hint}>{hint}</p>
      )}
      {error && (
        <p id={`${inputId}-error`} className={styles.field__error} role="alert">{error}</p>
      )}
    </div>
  );
});

export default Input;
