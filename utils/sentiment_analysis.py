# I have NO idea how this works
# I copied it from the tutorial
# However it works
import re
import string
import random
from nltk import FreqDist, classify, NaiveBayesClassifier
from nltk.corpus import twitter_samples, stopwords, movie_reviews
from nltk.tag import pos_tag
from nltk.stem.wordnet import WordNetLemmatizer
from nltk.sentiment.vader import SentimentIntensityAnalyzer

positive_tweets = twitter_samples.strings("positive_tweets.json")
negative_tweets = twitter_samples.strings("negative_tweets.json")
positive_reviews = movie_reviews.categories("pos")
negative_reviews = movie_reviews.categories("neg")
positive_tweets += positive_reviews
# negative_tweets += negative_reviews
positive_tweet_tokens = twitter_samples.tokenized("positive_tweets.json")
negative_tweet_tokens = twitter_samples.tokenized("negative_tweets.json")
text = twitter_samples.strings("tweets.20150430-223406.json")
tweet_tokens = twitter_samples.tokenized("positive_tweets.json")
stop_words = stopwords.words("english")


def lemmatize_sentence(_tokens):
    lemmatizer = WordNetLemmatizer()
    lemmatized_sentence = []
    for word, tag in pos_tag(_tokens):
        if tag.startswith("NN"):
            pos = "n"
        elif tag.startswith("VB"):
            pos = "v"
        else:
            pos = "a"
        lemmatized_sentence.append(lemmatizer.lemmatize(word, pos))
    return lemmatized_sentence


def remove_noise(_tweet_tokens, _stop_words=()):
    cleaned_tokens = []

    for token, tag in pos_tag(_tweet_tokens):
        token = re.sub("https?://(?:[a-zA-Z]|[0-9]|[$-_@.&+#]|[!*(),]|" "%[0-9a-fA-F][0-9a-fA-F])+", "", token)
        token = re.sub("(@[A-Za-z0-9_]+)", "", token)

        if tag.startswith("NN"):
            pos = "n"
        elif tag.startswith("VB"):
            pos = "v"
        else:
            pos = "a"

        lemmatizer = WordNetLemmatizer()
        token = lemmatizer.lemmatize(token, pos)

        if len(token) > 0 and token not in string.punctuation and token.lower() not in _stop_words:
            cleaned_tokens.append(token.lower())
    return cleaned_tokens


positive_cleaned_tokens_list = []
negative_cleaned_tokens_list = []

for tokens in positive_tweet_tokens:
    positive_cleaned_tokens_list.append(remove_noise(tokens, stop_words))

for tokens in negative_tweet_tokens:
    negative_cleaned_tokens_list.append(remove_noise(tokens, stop_words))


def get_all_words(cleaned_tokens_list):
    for _tokens in cleaned_tokens_list:
        for token in _tokens:
            yield token


all_pos_words = get_all_words(positive_cleaned_tokens_list)
freq_dist_pos = FreqDist(all_pos_words)


def get_tweets_for_model(cleaned_tokens_list):
    for _tweet_tokens in cleaned_tokens_list:
        yield {token: True for token in _tweet_tokens}


positive_tokens_for_model = get_tweets_for_model(positive_cleaned_tokens_list)
negative_tokens_for_model = get_tweets_for_model(negative_cleaned_tokens_list)

positive_dataset = [(tweet_dict, "Positive") for tweet_dict in positive_tokens_for_model]

negative_dataset = [(tweet_dict, "Negative") for tweet_dict in negative_tokens_for_model]

dataset = positive_dataset + negative_dataset

random.shuffle(dataset)

train_data = dataset[:7000]
test_data = dataset[7000:]
classifier = NaiveBayesClassifier.train(train_data)
intensity_analyser = SentimentIntensityAnalyzer()

if __name__ == "__main__":
    while True:
        try:
            ex = input("> ")
        except KeyboardInterrupt:
            break
        else:
            print(classifier.classify({token: True for token in remove_noise(ex.split())}))
            print(intensity_analyser.polarity_scores(ex))
