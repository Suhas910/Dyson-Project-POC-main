import { useState } from "react";
import {
  Alert,
  AlertTitle,
  Box,
  Collapse,
  IconButton,
  Typography,
} from "@mui/material";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import ExpandLessIcon from "@mui/icons-material/ExpandLess";

function ValidationIssuesPanel({ issues }) {
  const [expanded, setExpanded] = useState(true);

  if (!issues || issues.length === 0) return null;

  return (
    <Box sx={{ mb: 2 }}>
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          cursor: "pointer",
          userSelect: "none",
        }}
        onClick={() => setExpanded(!expanded)}
      >
        <Alert
          severity="warning"
          sx={{ flex: 1 }}
          action={
            <IconButton
              size="small"
              onClick={(e) => {
                e.stopPropagation();
                setExpanded(!expanded);
              }}
            >
              {expanded ? (
                <ExpandLessIcon fontSize="small" />
              ) : (
                <ExpandMoreIcon fontSize="small" />
              )}
            </IconButton>
          }
        >
          <AlertTitle>Validation Issues ({issues.length})</AlertTitle>
          {issues.length === 1
            ? "1 internal consistency issue detected during analysis."
            : `${issues.length} internal consistency issues detected during analysis.`}
        </Alert>
      </Box>
      <Collapse in={expanded}>
        <Box sx={{ mt: 1, ml: 1 }}>
          {issues.map((issue, index) => (
            <Alert key={index} severity="warning" sx={{ mb: 0.5 }}>
              <Typography variant="body2">
                <strong>{issue.finding}</strong>: {issue.issue}
              </Typography>
            </Alert>
          ))}
        </Box>
      </Collapse>
    </Box>
  );
}

export default ValidationIssuesPanel;
