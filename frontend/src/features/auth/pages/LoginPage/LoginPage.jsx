import { useEffect } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { useAppDispatch, useAppSelector } from '@app/hooks';
import { loginThunk } from '@features/auth/authThunks';
import { selectIsAuthenticated, selectAuthLoading, selectAuthError, clearError } from '@features/auth/authSlice';
import { loginSchema } from '@utils/validation';
import { ROUTES } from '@utils/constants';
import Button from '@components/common/Button/Button';
import styles from './LoginPage.module.scss';

const FEATURES = [
  { icon: '📚', title: 'Prompt Library', desc: 'Version-controlled prompt assets with team collaboration and performance tracking.' },
  { icon: '⚙️', title: 'AI Course Generation', desc: 'Generate lessons, assessments, and all components from a single Blueprint.' },
  { icon: '✏️', title: 'Block-Level Editing', desc: 'Edit, review, and regenerate individual content blocks without redoing the course.' },
  { icon: '📊', title: 'Analytics', desc: 'Track prompt quality, review scores, and system events in real-time dashboards.' },
  { icon: '📄', title: 'Multi-Format Export', desc: 'Export to Markdown, JSON, HTML, or Word DOCX — ready for any LMS.' },
  { icon: '🔍', title: 'Full Observability', desc: 'Every action is logged — generations, reviews, exports — for complete audit trails.' },
];

export default function LoginPage() {
  const dispatch   = useAppDispatch();
  const navigate   = useNavigate();
  const location   = useLocation();
  const isAuth     = useAppSelector(selectIsAuthenticated);
  const isLoading  = useAppSelector(selectAuthLoading);
  const serverError = useAppSelector(selectAuthError);

  const from = location.state?.from?.pathname || ROUTES.DASHBOARD;

  const { register, handleSubmit, formState: { errors } } = useForm({
    resolver: zodResolver(loginSchema),
  });

  useEffect(() => {
    if (isAuth) navigate(from, { replace: true });
  }, [isAuth, navigate, from]);

  useEffect(() => () => { dispatch(clearError()); }, [dispatch]);

  async function onSubmit(data) {
    dispatch(loginThunk(data));
  }

  return (
    <div className={styles.page}>
      <aside className={styles.sidebar}>
        <p className={styles.sidebar__label}>Sign In</p>
        <form onSubmit={handleSubmit(onSubmit)} className={styles.form} noValidate>
          {serverError && <div className={styles.error} role="alert">{serverError}</div>}
          <label className={styles.field}>
            Username
            <input type="text" autoComplete="username" autoFocus {...register('username')} className={styles.input} placeholder="Enter username" />
          </label>
          {errors.username && <span className={styles.fieldError}>{errors.username.message}</span>}
          <label className={styles.field}>
            Password
            <input type="password" autoComplete="current-password" {...register('password')} className={styles.input} placeholder="Enter password" />
          </label>
          {errors.password && <span className={styles.fieldError}>{errors.password.message}</span>}
          <Button type="submit" variant="primary" size="lg" fullWidth loading={isLoading}>
            Sign In
          </Button>
        </form>
      </aside>

      <main className={styles.main}>
        <div className={styles.hero}>
          <span className={styles.hero__badge}>Enterprise AI Platform</span>
          <h1 className={styles.hero__title}>
            <span className={styles.hero__logo} aria-hidden="true">🎓</span>
            Content AI Studio
          </h1>
          <p className={styles.hero__desc}>
            The centralised platform for AI-powered eLearning content creation.
            Manage prompts as code, enforce brand consistency, and generate production-ready courses at scale.
          </p>
        </div>

        <hr className={styles.divider} />

        <div className={styles.features}>
          {FEATURES.map((f) => (
            <div key={f.title} className={styles.featureCard}>
              <div className={styles.featureCard__icon}>{f.icon}</div>
              <div className={styles.featureCard__title}>{f.title}</div>
              <div className={styles.featureCard__desc}>{f.desc}</div>
            </div>
          ))}
        </div>
      </main>
    </div>
  );
}
