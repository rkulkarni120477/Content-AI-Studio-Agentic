import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { fetchStaffReviews } from '../../api/reviews';
import { starsDisplay } from '../../utils/prompt';
import { plPrompt } from '../../paths';

export default function AdminReviewsPage() {
  const [feedbackOnly, setFeedbackOnly] = useState(false);
  const [reviews, setReviews] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    void fetchStaffReviews(feedbackOnly).then((rows) => {
      setReviews(rows);
      setLoading(false);
    });
  }, [feedbackOnly]);

  return (
    <>
      <div className="page-header">
        <div>
          <h1>Prompt reviews</h1>
          <p className="subtitle">Ratings and feedback submitted by users.</p>
        </div>
        <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: '.85rem' }}>
          <input type="checkbox" checked={feedbackOnly} onChange={(e) => setFeedbackOnly(e.target.checked)} />
          With comments only
        </label>
      </div>

      <div className="page-card">
        {loading ? (
          <p style={{ color: 'var(--muted)' }}>Loading…</p>
        ) : (
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Prompt</th>
                  <th>User</th>
                  <th>Rating</th>
                  <th>Feedback</th>
                  <th>Date</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {reviews.length === 0 ? (
                  <tr>
                    <td colSpan={6} style={{ textAlign: 'center', color: 'var(--muted)', padding: 18 }}>
                      No reviews yet.
                    </td>
                  </tr>
                ) : (
                  reviews.map((r) => (
                    <tr key={r.id}>
                      <td>
                        <strong>{r.prompt_title || '—'}</strong>
                      </td>
                      <td>{r.username}</td>
                      <td>
                        <span className="stars">{starsDisplay(r.rating)}</span>
                        <span style={{ marginLeft: 6, fontSize: '.78rem', color: 'var(--muted)' }}>
                          {r.rating}/5
                        </span>
                      </td>
                      <td style={{ maxWidth: 280, fontSize: '.82rem' }}>
                        {r.feedback || <span style={{ color: 'var(--muted)' }}>—</span>}
                      </td>
                      <td style={{ whiteSpace: 'nowrap', fontSize: '.78rem' }}>
                        {r.updated_at ? new Date(r.updated_at).toLocaleDateString() : ''}
                      </td>
                      <td>
                        <Link to={plPrompt(r.prompt_id)} className="btn btn-ghost btn-sm">
                          View
                        </Link>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </>
  );
}
