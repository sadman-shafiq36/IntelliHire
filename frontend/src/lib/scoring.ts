import type { ScoreDimensionName } from '@/api/types'

export const DIMENSIONS: ScoreDimensionName[] = [
  'technical_skills',
  'relevant_experience',
  'qualifications',
  'jd_match',
]

export const DIMENSION_LABEL: Record<ScoreDimensionName, string> = {
  technical_skills: 'Technical skills',
  relevant_experience: 'Relevant experience',
  qualifications: 'Qualifications',
  jd_match: 'Description match',
}

export const DIMENSION_SHORT: Record<ScoreDimensionName, string> = {
  technical_skills: 'Skills',
  relevant_experience: 'Experience',
  qualifications: 'Qualif.',
  jd_match: 'Match',
}

export const DIMENSION_HELP: Record<ScoreDimensionName, string> = {
  technical_skills: 'Requirements about tools, languages and methods.',
  relevant_experience: 'Experience and responsibility requirements, and the years asked for.',
  qualifications: 'Degrees, certifications and formal training asked for.',
  jd_match:
    "Like an applicant tracking system: the description's key terms found in the CV, and how close its wording is.",
}
