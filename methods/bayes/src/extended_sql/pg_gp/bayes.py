import plpy

"""@file bayes.py

@brief Naive Bayes classification with user-defined smoothing factor (default:
Laplacian smoothing).

@namespace bayes

@defgroup modeling Modeling

@defgroup suplearn Supervised Learning
@ingroup modeling

@defgroup suplearn-categorization Categorization
@ingroup suplearn

@defgroup bayes Naive Bayes classification
@ingroup suplearn-categorization

@about

Naive Bayes classification with user-defined smoothing factor (default:
Laplacian smoothing).

A Naive Bayes classifier computes the following formula:
@verbatim
                                              __n
  classify(a_1, ..., a_n) = argmax_c P(C = c) ||    P(A_i = a_i | C = c)
                                                i=1
@endverbatim
where probabilites are estimated with relative frequencies from the training
set.  See also: http://en.wikipedia.org/wiki/Naive_Bayes_classifier

There are different ways to estimate the feature probabilities
$P(A_i = a_i | C = c)$.  The maximum likelihood exstimate takes the relative
frequencies. That is:
@verbatim

                       #(c,i,a)
  P(A_i = a | C = c) = --------
                          #c

where:
#(c,i,a) = # of training samples where attribute i is a and class is c
#c       = # of training samples where class is c
@endverbatim

Since the maximum likelihood sometimes results in estimates of 0, it might
be desirable to use a "smoothed" estimate. Intuitively, one adds a number of
"virtual" samples and assumes that these samples are evenly distributed
among the values attribute i can assume (i.e., the set of all values observed
for attribute a for any class):

@verbatim
                       #(c,i,a) + s
  P(A_i = a | C = c) = ------------
                        #c + s * #i

where:
#i       = # of distinct values for attribute i (for all classes)
s        = smoothing factor (>= 0).
@endverbatim

The case $s = 1$ is known as "Laplace smoothing" in the literature. The case
$s = 0$ trivially reduces to maximum likelihood estimates.

For the Naive Bayes Classification, we need a product over probabilities.
However, multiplying lots of small numbers can lead to an exponent overflow.
E.g., multiplying more than 324 numbers at most 0.1 will yield a product of 0
in machine arithmetic. A safer way is therefore summing logarithms.

By the IEEE 754 standard, the smallest number representable as
DOUBLE PRECISION (64bit) is $2^{-1022}$, i.e., approximately 2.225e-308.
See, e.g., http://en.wikipedia.org/wiki/Double_precision
Hence, log(x) = log_10(x) for any non-zero DOUBLE PRECISION @f$x \ge -308@f$.

Note for theorists:
- Even adding infinitely many @f$\log_{10}(x)@f$ for @f$0 < x \le 1@f$ will never cause
  an overflow because addition will have no effect once the sum reaches approx
  $308 * 2^{53}$ (correspnding to the machine precision).

@anchor bayes_description
All create_ functions can be called with two sets of arguments:

@internal

	@implementation
	
	The functions __get_*_sql are private because we do not want to commit ourselves
	to a particular interface. We might want to be able to change implementation
	details should the need arise.

@endinternal
"""

def __get_feature_probs_sql(**kwargs):
	"""Return SQL query with columns (class, attr, value, cnt, attr_cnt).
	
	For class c, attr i, and value a, cnt is #(c,i,a) and attr_cnt is \#i.
	
	Note that the query will contain a row for every pair (class, value)
	occuring in the training data (so it might also contain rows where
	\#(c,i,a) = 0).

	@param classPriorsSource Relation (class, class_cnt, all_cnt) where
		   class is c, class_cnt is \#c, all_cnt is the number of rows in
		   \em trainingSource
	@param attrValuesSource Relation (attr, value) containing all distinct
		   attribute, value pairs. If omitted, will use __get_attr_values_sql()
	@param attrCountsSource Relation (attr, attr_cnt) where attr is i and
		   attr_cnt is \#i. If omitted, will use __get_attr_counts_sql()
	@param trainingSource name of relation containing training data
	@param trainingClassColumn name of column with class
	@param trainingAttrColumn name of column with attributes array
	@param numAttrs Number of attributes to use for classification
	
	For meanings of \#(c,i,a), \#c, and \#i see the general description of
	\ref bayes.
	"""

	if not 'attrValuesSource' in kwargs:
		kwargs.update(dict(
				attrValuesSource = "(" + __get_attr_values_sql(**kwargs) + ")"
			))
	if not 'attrCountsSource' in kwargs:
		kwargs.update(dict(
				attrCountsSource = "(" + __get_attr_counts_sql(**kwargs) + ")"
			))

	return """
		SELECT
			class,
			attr,
			value,
			coalesce(cnt, 0) AS cnt,
			attr_cnt
		FROM
		(
			SELECT *
			FROM
				{classPriorsSource} AS classes
			CROSS JOIN
				{attrValuesSource} AS attr_values
		) AS required_triples
		LEFT OUTER JOIN
		(
			SELECT
				{trainingClassColumn} AS class,
				attr,
				{trainingAttrColumn}[attr] AS value,
				count(*) AS cnt
			FROM
				generate_series(1, {numAttrs}) AS attr,
				{trainingSource}
			GROUP BY class, attr, value
		) AS triple_counts
		USING (class, attr, value)
		INNER JOIN
			{attrCountsSource} AS attr_counts
		USING (attr)
		""".format(**kwargs)


def __get_attr_values_sql(**kwargs):
	"""
	Return SQL query with columns (attr, value).
	
	The query contains a row for each pair that occurs in the training data.

	@param trainingSource Name of relation containing the training data
	@param trainingAttrColumn Name of attributes-array column in training data	
	@param numAttrs Number of attributes to use for classification

	@internal
	\par Implementation Notes:
	If PostgreSQL supported count(DISTINCT ...) for window functions, we could
	consolidate this function with __get_attr_counts_sql():
	@verbatim
	[...] count(DISTINCT value) OVER (PARTITION BY attr) [...]
	@endverbatim
	@endinternal
	
	"""

	return """
		SELECT DISTINCT
			attr,
			{trainingAttrColumn}[attr] AS value
		FROM
			generate_series(1, {numAttrs}) AS attr,
			{trainingSource}
		""".format(**kwargs)


def __get_attr_counts_sql(**kwargs):
	"""
	Return SQL query with columns (attr, attr_cnt)
	
	For attr i, attr_cnt is \#i.
	
	@param trainingSource Name of relation containing the training data
	@param trainingAttrColumn Name of attributes-array column in training data	
	@param numAttrs Number of attributes to use for classification
	
	"""

	return """
		SELECT
			attr,
			count(DISTINCT {trainingAttrColumn}[attr]) AS attr_cnt
		FROM
			generate_series(1, {numAttrs}) AS attr,
			{trainingSource}
		GROUP BY attr
		""".format(**kwargs)


def __get_class_priors_sql(**kwargs):
	"""
	Return SQL query with columns (class, class_cnt, all_cnt)
	
	For class c, class_cnt is \#c. all_cnt is the total number of records in the
	training data.

	@param trainingSource Name of relation containing the training data
	@param trainingClassColumn Name of class column in training data	
	
	"""

	return """
		SELECT
			{trainingClassColumn} AS class,
			count(*) AS class_cnt,
			sum(count(*)) OVER () AS all_cnt
		FROM {trainingSource}
		GROUP BY class
		""".format(**kwargs)


def __get_keys_and_prob_values_sql(**kwargs):
	"""
	Return SQL query with columns (key, class, log_prob).
	
	For class c and the attribute array identified by key k, log_prob is
	log( P(C = c) * P(A = a(k)[] | C = c) ).
	
	For each key k and class c, the query also contains a row (k, c, NULL). This
	is for technical reasons (we want every key-class pair to appear in the
	query. NULL serves as a default value if there is insufficient training data
	to compute a probability value).

	@param numAttrs Number of attributes to use for classification
	@param classifySource Name of the relation that contains data to be classified
	@param classifyKeyColumn Name of column in \em classifySource that can
		   serve as unique identifier
	@param classifyAttrColumn Name of attributes-array column in \em classifySource
	@param classPriorsSource
		   Relation (class, class_cnt, all_cnt) where
		   class is c, class_cnt is \#c, all_cnt is the number of training
		   samples.
	@param featureProbsSource
		   Relation (class, attr, value, cnt, attr_cnt) where
		   (class, attr, value) = (c,i,a), cnt = \#(c,i,a), and attr_cnt = \#i
	@param smoothingFactor Smoothing factor for computing feature
		   feature probabilities. Default value: 1.0 (Laplacian Smoothing).

	"""

	return """
	SELECT
		classify.key,
		classPriors.class,
		CASE WHEN count(*) < {numAttrs} THEN NULL
			 ELSE
				log(classPriors.class_cnt::DOUBLE PRECISION / classPriors.all_cnt)
				+ sum( log((featureProbs.cnt::DOUBLE PRECISION + {smoothingFactor})
					/ (classPriors.class_cnt + {smoothingFactor} * featureProbs.attr_cnt)) )
			 END
		AS log_prob
	FROM
		{featureProbsSource} AS featureProbs,
		{classPriorsSource} AS classPriors,
		(
			SELECT
				{classifyKeyColumn} AS key,
				attr,
				{classifyAttrColumn}[attr] AS value
			FROM
				{classifySource},
				generate_series(1, {numAttrs}) AS attr
		) AS classify
	WHERE
		featureProbs.class = classPriors.class AND
		featureProbs.attr = classify.attr AND
		featureProbs.value = classify.value AND
		({smoothingFactor} > 0 OR featureProbs.cnt > 0) -- prevent division by 0
	GROUP BY
		classify.key, classPriors.class, classPriors.class_cnt, classPriors.all_cnt
	
	UNION
	
	SELECT
		classify.{classifyKeyColumn} AS key,
		classes.class,
		NULL
	FROM
		{classifySource} AS classify,
		{classPriorsSource} AS classes
	GROUP BY key, class
	""".format(**kwargs)


def __get_prob_values_sql(**kwargs):
	"""
	Return SQL query with columns (class, log_prob), given an array of
	attributes.
	
	The query binds to an attribute array a[]. For every class c, log_prob
	is log( P(C = c) * P(A = a[] | C = c) ).
	
	@param classifyAttrColumn Array of attributes to bind to. This can be
		   a column name of an outer query or a literal.
	@param smoothingFactor Smoothing factor to use for estimating the feature
		   probabilities.
	@param numAttrs Number of attributes to use for classification
	@param classPriorsSource
		   Relation (class, class_cnt, all_cnt) where
		   class is c, class_cnt is \#c, all_cnt is the number of training
		   samples.
	@param featureProbsSource
		   Relation (class, attr, value, cnt, attr_cnt) where
		   (class, attr, value) = (c,i,a), cnt = \#(c,i,a), and attr_cnt = \#i
	
	Note that unless \em classifyAttrColumn is a literal, the SQL query will
	become a correlated subquery and will not work in Greenplum.

	"""

	return """
	SELECT
		classPriors.class,
		CASE WHEN count(*) < {numAttrs} THEN NULL
			 ELSE
				log(classPriors.class_cnt::DOUBLE PRECISION / classPriors.all_cnt)
				+ sum( log((featureProbs.cnt::DOUBLE PRECISION + {smoothingFactor})
					/ (classPriors.class_cnt + {smoothingFactor} * featureProbs.attr_cnt)) )
			 END
		AS log_prob
	FROM
		{featureProbsSource} AS featureProbs,
		{classPriorsSource} AS classPriors,
		(
			SELECT
				attr,
				{classifyAttrColumn}[attr] AS value
			FROM
				generate_series(1, {numAttrs}) AS attr
		) AS classify
	WHERE
		featureProbs.class = classPriors.class AND
		featureProbs.attr = classify.attr AND featureProbs.value = classify.value AND
		({smoothingFactor} > 0 OR featureProbs.cnt > 0) -- prevent division by 0
	GROUP BY classPriors.class, classPriors.class_cnt, classPriors.all_cnt
	
	UNION
	
	SELECT
		classes.class,
		NULL
	FROM
		{classPriorsSource} AS classes
	""".format(**kwargs)


def __get_classification_sql(**kwargs):
	"""
	Return SQL query with columns (key, nb_classification, nb_log_probability)
	
	@param keys_and_prob_values Relation (key, class, log_prob)
	
	"""

	return """
		SELECT
			key,
			madlib.argmax(class, log_prob) AS nb_classification,
			max(log_prob) AS nb_log_probability
		FROM {keys_and_prob_values} AS keys_and_nb_values
		GROUP BY key
		""".format(
			keys_and_prob_values = "(" + __get_keys_and_prob_values_sql(**kwargs) + ")"
		)


def create_prepared_data(**kwargs):
	"""Precompute all class priors and feature probabilities.
	
	When the precomputations are stored in a table, this function will create
	indices that speed up lookups necessary for Naive Bayes classification.
	Moreover, it runs ANALYZE on the new tables to allow for optimized query
	plans.
	
	Class priors are stored in a relation with columns
	(class, class_cnt, all_cnt).
	
	@param trainingSource Name of relation containing the training data
	@param trainingClassColumn Name of class column in training data
	@param trainingAttrColumn Name of attributes-array column in training data	
	@param numAttrs Number of attributes to use for classification
	
	@param whatToCreate (Optional) Either \c 'TABLE' OR \c 'VIEW' (the default).
	@param classPriorsDestName Name of class-priors relation to create
	@param featureProbsDestName Name of feature-probabilities relation to create 
		
	"""
	
	if kwargs['whatToCreate'] == 'TABLE':
		# FIXME: ANALYZE is not portable.
		plpy.execute("""
			CREATE TEMPORARY TABLE tmp_attr_counts
			AS
			{attr_counts_sql};
			ALTER TABLE tmp_attr_counts ADD PRIMARY KEY (attr);
			ANALYZE tmp_attr_counts;
			
			CREATE TEMPORARY TABLE tmp_attr_values
			AS
			{attr_values_sql};
			ALTER TABLE tmp_attr_values ADD PRIMARY KEY (attr, value);
			ANALYZE tmp_attr_values;
			""".format(
				attr_counts_sql = "(" + __get_attr_counts_sql(**kwargs) + ")",
				attr_values_sql = "(" + __get_attr_values_sql(**kwargs) + ")"
				)
			)
		kwargs.update(dict(
			attrValuesSource = 'tmp_attr_values',
			attrCountsSource = 'tmp_attr_counts'
		))


	kwargs.update(dict(
			sql = __get_class_priors_sql(**kwargs)
		))
	plpy.execute("""
		CREATE {whatToCreate} {classPriorsDestName}
		AS
		{sql}
		""".format(**kwargs)
		)
	if kwargs['whatToCreate'] == 'TABLE':
		plpy.execute("""
			ALTER TABLE {classPriorsDestName} ADD PRIMARY KEY (class);
			ANALYZE {classPriorsDestName};
			""".format(**kwargs))
	
	kwargs.update(dict(
			classPriorsSource = kwargs['classPriorsDestName']
		))
	kwargs.update(dict(
			sql = __get_feature_probs_sql(**kwargs)
		))
	plpy.execute("""
		CREATE {whatToCreate} {featureProbsDestName} AS
		{sql}
		""".format(**kwargs)
		)
	if kwargs['whatToCreate'] == 'TABLE':
		plpy.execute("""
			ALTER TABLE {featureProbsDestName} ADD PRIMARY KEY (class, attr, value);
			ANALYZE {featureProbsDestName};
			DROP TABLE tmp_attr_counts;
			DROP TABLE tmp_attr_values;
			""".format(**kwargs))


def create_classification(**kwargs):
	"""
	Create a view/table with columns (key, nb_classification).
	
	The created relation will be
	
	<tt>{TABLE|VIEW} <em>destName</em> (key, nb_classification)</tt>
	
	where \c nb_classification is an array containing the most likely
	class(es) of the record in \em classifySource identified by \c key.

	There are two sets of arguments this function can be called with. The
	following parameters are always needed:
	@param numAttrs Number of attributes to use for classification
	@param destName Name of the table or view to create
	@param whatToCreate (Optional) Either \c 'TABLE' OR \c 'VIEW' (the default).
	@param smoothingFactor (Optional) Smoothing factor for computing feature
		   feature probabilities. Default value: 1.0 (Laplacian Smoothing).
	@param classifySource Name of the relation that contains data to be classified
	@param classifyKeyColumn Name of column in \em classifySource that can
		   serve as unique identifier
	@param classifyAttrColumn Name of attributes-array column in \em classifySource

	Furthermore, provide either:
	@param classPriorsSource
		   Relation (class, class_cnt, all_cnt) where
		   class is c, class_cnt is \#c, all_cnt is the number of training
		   samples.
	@param featureProbsSource
		   Relation (class, attr, value, cnt, attr_cnt) where
		   (class, attr, value) = (c,i,a), cnt = \#(c,i,a), and attr_cnt = \#i

	Or have this function operate on the "raw" training data:
	@param trainingSource
		   Name of relation containing the training data
	@param trainingClassColumn
		   Name of class column in training data
	@param trainingAttrColumn
		   Name of attributes-array column in \em trainingSource	

	"""
	
	__init_prepared_data(kwargs)
	kwargs.update(dict(
		keys_and_prob_values = "(" + __get_keys_and_prob_values_sql(**kwargs) + ")"
		))
	plpy.execute("""
		CREATE {whatToCreate} {destName} AS
		SELECT
			key,
			madlib.argmax(class, log_prob) AS nb_classification
		FROM {keys_and_prob_values} AS keys_and_nb_values
		GROUP BY key
		""".format(**kwargs))


def create_bayes_probabilities(**kwargs):
	"""Create table/view with columns (key, class, nb_prob).
	
	The created relation will be
	
	<tt>{TABLE|VIEW} <em>destName</em> (key, class, nb_prob)</tt>
	
	where \c nb_prob is the Naive-Bayes probability that \c class is the true
	class of the record in \em classifySource identified by \c key.
	
	There are two sets of arguments this function can be called with. The
	following parameters are always needed:
	@param numAttrs Number of attributes to use for classification
	@param destName Name of the table or view to create
	@param whatToCreate (Optional) Either \c 'TABLE' OR \c 'VIEW' (the default).
	@param smoothingFactor (Optional) Smoothing factor for computing feature
		   feature probabilities. Default value: 1.0 (Laplacian Smoothing).

	Furthermore, provide either:
	@param classPriorsSource
		   Relation (class, class_cnt, all_cnt) where
		   class is c, class_cnt is \#c, all_cnt is the number of training
		   samples.
	@param featureProbsSource
		   Relation (class, attr, value, cnt, attr_cnt) where
		   (class, attr, value) = (c,i,a), cnt = \#(c,i,a), and attr_cnt = \#i

	Or have this function operate on the "raw" training data:
	@param trainingSource
		   Name of relation containing the training data
	@param trainingClassColumn
		   Name of class column in training data
	@param trainingAttrColumn
		   Name of attributes-array column in training data	
	
	@internal
	\par Implementation Notes:

	We have two numerical problems when copmuting the probabilities
	@verbatim
               P(C = c) * P(A = a | C = c)
 P(C = c) = ---------------------------------    (*)
            --
            \   P(C = c') * P(A = a | C = c')
            /_ 
              c'
                         __
where P(A = a | C = c) = ||  P(A_i = a_i | C = c).
                           i
	@endverbatim

	1. P(A = a | C = c) could be a very small number not representable in
	   double-precision floating-point arithmetic.
	   - Solution: We have log( P(C = c) * P(A = a | C = c) ) as indermediate
	     results. We will add the maximum absolute value of these intermediate
	     results to all of them. This corresponds to multiplying numerator and
	     denominator of (*) with the same factor. The "normalization" ensures
	     that the numerator of (*) can never be 0 (in FP arithmetic) for all c.

	2. PostgreSQL raises an error in case of underflows, even when 0 is the
	   desirable outcome.
	   - Solution: if log_10 ( P(A = a | C = c) ) < -300, we interprete
         P(A = a | C = c) = 0. Note here that 1e-300 is roughly in the order of
	     magnitude of the smallest double precision FP number.
	@endinternal
	"""

	__init_prepared_data(kwargs)
	kwargs.update(dict(
		keys_and_prob_values = "(" + __get_keys_and_prob_values_sql(**kwargs) + ")"
		))
	plpy.execute("""
		CREATE {whatToCreate} {destName} AS
		SELECT
			key,
			class,
			nb_prob / sum(nb_prob) OVER (PARTITION BY key) AS nb_prob
		FROM
		(
			SELECT
				key,
				class,
				CASE WHEN max(log_prob) - max(max(log_prob)) OVER (PARTITION BY key) < -300 THEN 0
					 ELSE pow(10, max(log_prob) - max(max(log_prob)) OVER (PARTITION BY key))
				END AS nb_prob
			FROM
				{keys_and_prob_values} AS keys_and_nb_values
			GROUP BY
				key, class
		) AS keys_and_nb_values
		ORDER BY
			key, class
		""".format(**kwargs))


def create_classification_function(**kwargs):
	"""Create a SQL function mapping arrays of attribute values to the Naive
	Bayes classification.
	
	The created SQL function will be:
	
	<tt>
	FUNCTION <em>destName</em> (attributes INTEGER[], smoothingFactor DOUBLE PRECISION)
	RETURNS INTEGER[]</tt>
	
	There are two sets of arguments this function can be called with. The
	following parameters are always needed:
	@param classifyAttrColumn Array of attributes to bind to. This can be
		   a column name of an outer query or a literal.
	@param smoothingFactor Smoothing factor to use for estimating the feature
		   probabilities.
	@param numAttrs Number of attributes to use for classification

	Furthermore, provide either:
	@param classPriorsSource
		   Relation (class, class_cnt, all_cnt) where
		   class is c, class_cnt is \#c, all_cnt is the number of training
		   samples.
	@param featureProbsSource
		   Relation (class, attr, value, cnt, attr_cnt) where
		   (class, attr, value) = (c,i,a), cnt = \#(c,i,a), and attr_cnt = \#i

	Or have this function operate on the "raw" training data:
	@param trainingSource Name of relation containing the training data
	@param trainingClassColumn Name of class column in training data
	@param trainingAttrColumn Name of attributes-array column in training data	
	
	Note: Greenplum does not support executing STABLE and VOLATILE functions on
	segments. The created function can therefore only be called on the master.
	"""
	
	kwargs.update(dict(
		classifyAttrColumn = "$1",
		smoothingFactor = "$2"
		))
	__init_prepared_data(kwargs)
	kwargs.update(dict(
		keys_and_prob_values = "(" + __get_prob_values_sql(**kwargs) + ")"
		))
	plpy.execute("""
		CREATE FUNCTION {destName} (inAttributes INTEGER[], inSmoothingFactor DOUBLE PRECISION)
		RETURNS INTEGER[] AS
		$$
			SELECT
				madlib.argmax(class, log_prob)
			FROM {keys_and_prob_values} AS key_and_nb_values
		$$
		LANGUAGE sql STABLE
		""".format(**kwargs))


def __init_prepared_data(kwargs):
	"""
	Fill in values for optional parameters: Create subqueries instead of using
	a relation.

	"""

	if not 'classPriorsSource' in kwargs:
		kwargs.update(dict(
				classPriorsSource = "(" + __get_class_priors_sql(**kwargs) + ")" 
			))
	if not 'featureProbsSource' in kwargs:
		kwargs.update(dict(
				featureProbsSource = "(" + __get_feature_probs_sql(**kwargs) + ")"
			))
	if not 'smoothingFactor' in kwargs:
		kwargs.update(dict(
				smoothingFactor = 1
			))
	

